import logging
import json
import asyncio
import warnings

# 忽略 lark_oapi 的弃用警告，避免日志污染
warnings.filterwarnings('ignore', category=UserWarning, module='lark_oapi')

from flask import Flask
import lark_oapi as lark
from lark_oapi.adapter.flask import parse_req
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageResponse, CreateMessageRequestBody, P2ImMessageReceiveV1
import redis

from config_manager import ConfigManager
from deepseek_client import DeepSeekClient

app = Flask(__name__)
config = ConfigManager()

# 配置日志，生产环境记录到文件，开发环境仅输出到控制台
log_level = config.get_log_level()
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/app.log'),
        logging.StreamHandler()
    ] if config.is_production() else [logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 记录当前环境
logger.info(f'应用启动，运行环境: {"生产环境" if config.is_production() else "开发环境"}')

# 初始化 Redis 客户端连接池，提升连接复用效率
redis_pool = redis.ConnectionPool(
    host=config.get('REDIS_HOST'),
    port=int(config.get('REDIS_PORT', 6379)),
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30,
    max_connections=10
)
redis_client = redis.Redis(connection_pool=redis_pool)

# 检查 Redis 连接，重试机制提升健壮性
redis_connection_retry = 3
connected = False
for i in range(redis_connection_retry):
    try:
        redis_client.ping()
        logger.info("成功连接到Redis服务")
        connected = True
        break
    except redis.ConnectionError as e:
        logger.warning(f"Redis连接尝试 {i+1}/{redis_connection_retry} 失败: {str(e)}")
        if i < redis_connection_retry - 1:
            import time
            time.sleep(1)

if not connected:
    logger.error("无法连接到Redis服务，请检查配置和服务状态")
    if config.is_production():
        exit(1)
    else:
        logger.warning("开发环境下Redis连接失败，继续运行但部分功能可能受限")

# 事件ID过期时间（秒），合理设置避免Redis空间占用过大
EVENT_EXPIRE_SECONDS = 3600

# Redis操作最大重试次数，提升分布式去重健壮性
REDIS_MAX_RETRIES = 3

# Redis重试间隔（秒），防止频繁重试导致性能下降
REDIS_RETRY_INTERVAL = 0.1

# 启动前检查必要配置，缺失则直接退出，保证服务安全
required_configs = ['DEEPSEEK_API_KEY', 'FEISHU_ENCRYPT_KEY', 'FEISHU_VERIFICATION_TOKEN', 'FEISHU_APP_ID', 'FEISHU_APP_SECRET']
if config.is_production():
    required_configs.extend(['REDIS_HOST', 'REDIS_PORT'])

missing = [key for key in required_configs if not config.get(key)]
if missing:
    logger.error(f"缺少必要配置: {', '.join(missing)}")
    exit(1)

ds_client = DeepSeekClient(config.get('DEEPSEEK_API_KEY'), config.get('DEEPSEEK_API_URL', 'https://api.deepseek.com/v1'))

# 初始化飞书客户端，统一日志等级
client = lark.Client.builder() \
    .app_id(config.get('FEISHU_APP_ID')) \
    .app_secret(config.get('FEISHU_APP_SECRET')) \
    .log_level(lark.LogLevel.INFO) \
    .build()

def get_sender_open_id(data):
    """获取发送者的 open_id，兼容不同 SDK 版本，优先返回 open_id，其次 user_id"""
    try:
        if hasattr(data, 'event'):
            if hasattr(data.event, 'sender'):
                if hasattr(data.event.sender, 'sender_id'):
                    if hasattr(data.event.sender.sender_id, 'open_id'):
                        return data.event.sender.sender_id.open_id
                    elif hasattr(data.event.sender.sender_id, 'user_id'):
                        return data.event.sender.sender_id.user_id
                    raise AttributeError("sender_id对象缺少open_id或user_id属性")
                elif hasattr(data.event.sender, 'open_id'):
                    return data.event.sender.open_id
                elif hasattr(data.event.sender, 'user_id'):
                    return data.event.sender.user_id
                raise AttributeError("sender对象缺少必要的ID属性")
            elif hasattr(data.event, 'user_id'):
                return data.event.user_id
            raise AttributeError("event对象缺少发送者信息")
        elif hasattr(data, 'sender'):
            if hasattr(data.sender, 'sender_id') and hasattr(data.sender.sender_id, 'open_id'):
                return data.sender.sender_id.open_id
            elif hasattr(data.sender, 'open_id'):
                return data.sender.open_id
            raise AttributeError("sender对象缺少必要的ID属性")
        elif hasattr(data, 'user_id'):
            return data.user_id
        raise AttributeError("无法找到发送者ID属性")
    except AttributeError as e:
        logger.error(f"无法获取发送者ID: {str(e)}")
        return None

async def send_chunk(chunk, user_open_id):
    """发送流式回复的 chunk，失败自动记录日志"""
    try:
        content = chunk.get('content', '')
        if content and user_open_id:
            message_content = json.dumps({"text": content})
            req = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(user_open_id)
                    .msg_type("text")
                    .content(message_content)
                    .build()) \
                .build()
            resp: CreateMessageResponse = client.im.v1.message.create(req)
            if resp.code != 0:
                logger.error(f"发送流式回复chunk失败: {resp.msg}")
            else:
                logger.info(f"流式回复chunk发送成功，消息ID: {resp.data.message_id}")
    except Exception as e:
        logger.error(f"发送流式回复chunk异常: {str(e)}")

async def process_message(user_msg, context=None, user_open_id=None, ds_client=None):
    """异步处理消息，自动选择 DeepSeek 客户端"""
    try:
        client = ds_client or globals().get('ds_client')
        if not client:
            raise ValueError('DeepSeek client not available')
        # 统一温度参数，关闭流式
        return await client.reason(user_msg, context=context, temperature=0.3, stream=False)
    except Exception as e:
        logger.exception(f"处理消息时发生异常: {str(e)}")
        return '服务暂时不可用，请稍后再试'

async def async_do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1):
    """异步处理飞书消息事件，包含去重、指令解析、上下文维护和异常记录"""
    try:
        # 获取事件ID，优先使用 event_id，其次 message_id
        try:
            event_id = None
            if hasattr(data, 'event') and hasattr(data.event, 'event_id'):
                event_id = data.event.event_id
            elif hasattr(data, 'event_id'):
                event_id = data.event_id
            if not event_id and hasattr(data, 'event') and hasattr(data.event, 'message') and hasattr(data.event.message, 'message_id'):
                event_id = data.event.message.message_id
            if not event_id:
                raise AttributeError("无法找到事件ID或消息ID")
        except AttributeError as e:
            logger.error(f"无法获取事件ID: {str(e)}")
            return None

        # Redis 分布式去重，防止重复处理事件，重试机制提升健壮性
        redis_key = f"event:{event_id}"
        logger.debug(f"尝试设置Redis去重键: {redis_key}")
        result = False
        retry_count = 0
        while retry_count < REDIS_MAX_RETRIES and not result:
            try:
                result = redis_client.set(redis_key, "processed", ex=EVENT_EXPIRE_SECONDS, nx=True)
                retry_count += 1
                if not result and retry_count < REDIS_MAX_RETRIES:
                    logger.debug(f"Redis设置失败，准备重试 ({retry_count}/{REDIS_MAX_RETRIES})")
                    await asyncio.sleep(REDIS_RETRY_INTERVAL)
            except redis.ConnectionError as e:
                logger.error(f"Redis连接错误: {str(e)}")
                retry_count += 1
                if retry_count < REDIS_MAX_RETRIES:
                    await asyncio.sleep(REDIS_RETRY_INTERVAL)
            except Exception as e:
                logger.error(f"Redis去重操作异常: {str(e)}")
                break
        logger.debug(f"Redis设置结果: {result}")
        if not result:
            logger.warning(f"事件 {event_id} 已处理或Redis设置失败，跳过重复处理")
            return None
        logger.info(f"事件 {event_id} 标记为已处理")

        # 验证消息类型，仅处理文本消息
        if data.event.message.message_type != 'text':
            logger.warning(f"忽略非文本消息: {data.event.message.message_type}")
            return None

        # 解析消息内容，异常自动记录
        raw_content = data.event.message.content
        if not raw_content:
            logger.error("收到空消息内容")
            return None
        try:
            content_dict = json.loads(raw_content)
            user_msg = content_dict.get('text', '').strip()
        except (TypeError, json.JSONDecodeError) as e:
            logger.error(f"解析消息内容失败: {e}")
            user_msg = ''
        if not user_msg:
            return None

        # 指令消息处理，支持余额、清除上下文、帮助
        if user_msg.strip() == "查询余额" or user_msg.strip().startswith("/查询余额"):
            try:
                balance_info = await ds_client.get_balance()
                is_available = balance_info.get("is_available", False)
                balance_details = balance_info.get("balance_infos", [])
                reply = f"账户余额状态: {'有可用余额' if is_available else '无可用余额'}\n"
                if balance_details:
                    reply += "余额详情:\n"
                    for detail in balance_details:
                        currency = detail.get('currency', '未知货币')
                        total_balance = detail.get('total_balance', '0')
                        granted_balance = detail.get('granted_balance', '0')
                        topped_up_balance = detail.get('topped_up_balance', '0')
                        reply += f"- 货币类型: {currency}\n"
                        reply += f"- 总余额: {total_balance} {currency}\n"
                        reply += f"- 赠额余额: {granted_balance} {currency}\n"
                        reply += f"- 充值余额: {topped_up_balance} {currency}\n"
                else:
                    reply += "暂无余额详情"
            except Exception as e:
                reply = f"查询余额失败: {str(e)}"
        elif user_msg.strip().startswith("/清除上下文"):
            try:
                user_open_id = get_sender_open_id(data)
                if user_open_id:
                    redis_key = f"context:{user_open_id}"
                    redis_client.delete(redis_key)
                    reply = "🧹对话上下文已清除"
                else:
                    reply = "无法获取用户信息，清除上下文失败"
            except Exception as e:
                reply = f"清除上下文失败: {str(e)}"
        elif user_msg.strip().startswith("/帮助") or user_msg.strip().startswith("/help") or user_msg.strip().startswith("/指定"):
            reply = "🤖 机器人指令说明\n\n"
            reply += "📌 查询余额\n"
            reply += "   指令: /查询余额\n"
            reply += "   功能: 查询DeepSeek API账户余额\n\n"
            reply += "📌 清除上下文\n"
            reply += "   指令: /清除上下文\n"
            reply += "   功能: 清除当前对话的上下文历史\n\n"
            reply += "📌 帮助\n"
            reply += "   指令: /帮助 或 /help 或 /指定\n"
            reply += "   功能: 查看所有可用指令说明\n\n"
            reply += "💡 提示: 直接发送消息即可进行正常对话，机器人会自动维护上下文"
        else:
            # 普通消息处理，自动维护上下文，异常自动记录
            try:
                user_open_id = get_sender_open_id(data)
                context = []
                if user_open_id:
                    redis_key = f"context:{user_open_id}"
                    context_str = redis_client.get(redis_key)
                    if context_str:
                        try:
                            context = json.loads(context_str)
                        except json.JSONDecodeError:
                            logger.error(f"解析上下文失败，用户: {user_open_id}")
                            context = []
                response = await process_message(user_msg, context=context, user_open_id=user_open_id)
                if response and user_open_id:
                    logger.debug(f"准备发送回复给用户 {user_open_id}: {response[:30]}...")
                    reply = response
                    # 更新上下文，最多保留10条
                    new_context = context.copy()
                    new_context.append({"role": "user", "content": user_msg})
                    if len(new_context) > 10:
                        new_context = new_context[-10:]
                    redis_client.set(redis_key, json.dumps(new_context), ex=86400)
                else:
                    reply = None
            except Exception as e:
                logger.exception(f"处理消息时发生异常: {str(e)}")
                reply = '服务暂时不可用，请稍后再试'
                # 记录异常事件，便于后续排查
                try:
                    error_event_key = f"error:event:{event_id}"
                    error_info = {
                        'timestamp': asyncio.get_event_loop().time(),
                        'error_type': type(e).__name__,
                        'error_message': str(e),
                        'user_open_id': user_open_id,
                        'user_msg': user_msg
                    }
                    redis_client.set(error_event_key, json.dumps(error_info), ex=86400)
                except Exception as redis_e:
                    logger.error(f"记录异常事件到Redis失败: {str(redis_e)}")

        # 发送消息，失败自动重试，最多3次
        if reply is not None:
            user_open_id = get_sender_open_id(data)
            if not user_open_id:
                logger.error("无法获取发送者ID，消息发送失败")
                return None
            max_retries = 3
            retry_count = 0
            sent_successfully = False
            message_id = None
            while retry_count < max_retries and not sent_successfully:
                try:
                    req = CreateMessageRequest.builder() \
                        .receive_id_type("open_id") \
                        .request_body(CreateMessageRequestBody.builder()
                            .receive_id(user_open_id)
                            .msg_type("text")
                            .content(json.dumps({"text": reply}))
                            .build()) \
                        .build()
                    resp: CreateMessageResponse = client.im.v1.message.create(req)
                    if resp.code != 0:
                        logger.error(f"发送消息失败: {resp.msg}, 错误码: {resp.code}, 重试次数: {retry_count+1}/{max_retries}")
                        retry_count += 1
                        if retry_count < max_retries:
                            await asyncio.sleep(0.2)
                    else:
                        message_id = resp.data.message_id
                        logger.info(f"消息发送成功，消息ID: {message_id}, 用户: {user_open_id}")
                        sent_successfully = True
                except Exception as e:
                    logger.error(f"发送消息异常: {str(e)}, 重试次数: {retry_count+1}/{max_retries}")
                    retry_count += 1
                    if retry_count < max_retries:
                        await asyncio.sleep(0.2)
            if not sent_successfully:
                logger.error(f"消息发送失败，已达到最大重试次数 ({max_retries})")
        return None
    except Exception as e:
        logger.exception(f"处理消息时发生异常: {str(e)}")
        return None

# 创建全局事件循环，避免每次请求创建新循环，提升性能
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1):
    """同步处理飞书消息事件，封装异步主逻辑"""
    try:
        return loop.run_until_complete(async_do_p2_im_message_receive_v1(data))
    except Exception as e:
        logger.exception(f"处理消息时发生异常: {str(e)}")
        return {'msg_type': 'text', 'content': {'text': '服务暂时不可用，请稍后再试'}}

# 初始化事件处理器，注册消息回调
handler = lark.EventDispatcherHandler.builder(
    config.get('FEISHU_ENCRYPT_KEY'),
    config.get('FEISHU_VERIFICATION_TOKEN'),
    lark.LogLevel.INFO
).register_p2_im_message_receive_v1(do_p2_im_message_receive_v1).build()

@app.route('/feishu/callback', methods=['POST'])
def handle_feishu():
    """处理飞书回调请求，异常自动记录"""
    try:
        req = parse_req()
        handler.do(req)
        return "OK", 200
    except Exception as e:
        logger.exception("处理飞书回调时发生异常")
        return "Server Error", 500

if __name__ == '__main__':
    port = int(config.get('PORT', 5000))
    logger.info(f"启动服务在端口 {port}")
    server_type = config.get('SERVER', 'waitress')
    # 根据配置选择服务器类型，生产环境推荐 waitress
    if server_type == 'waitress':
        from waitress import serve
        logger.info("使用Waitress服务器 (同步模式)")
        serve(app, host='0.0.0.0', port=port, threads=4)
    elif server_type == 'gunicorn':
        logger.warning("Gunicorn服务器需要通过命令行启动，此处将使用Flask开发服务器")
        app.run(host='0.0.0.0', port=port)
    else:
        logger.warning("使用Flask开发服务器 - 仅限测试环境")
        app.run(host='0.0.0.0', port=port)