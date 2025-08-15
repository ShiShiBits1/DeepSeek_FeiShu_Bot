import aiohttp
import json
import logging

logger = logging.getLogger(__name__)

class DeepSeekClient:
    def __init__(self, api_key, api_url):
        self.api_key = api_key
        self.api_url = api_url
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        # // 添加余额查询API地址
        self.balance_api_url = "https://api.deepseek.com/user/balance"

    def _handle_http_error(self, status_code, response_text):
        """处理HTTP错误状态码"""
        error_info = {
            400: ("格式错误", "请求体格式错误", "请根据错误信息提示修改请求体"),
            401: ("认证失败", "API key 错误，认证失败", "请检查您的 API key 是否正确，如没有 API key，请先 创建 API key"),
            402: ("余额不足", "账号余额不足", "请确认账户余额，并前往 充值 页面进行充值"),
            422: ("参数错误", "请求体参数错误", "请根据错误信息提示修改相关参数"),
            429: ("请求速率达到上限", "请求速率（TPM 或 RPM）达到上限", "请合理规划您的请求速率。"),
            500: ("服务器故障", "服务器内部故障", "请等待后重试。若问题一直存在，请联系我们解决"),
            503: ("服务器繁忙", "服务器负载过高", "请稍后重试您的请求")
        }
        
        if status_code in error_info:
            error_name, error_reason, solution = error_info[status_code]
            error_msg = f"DeepSeek API {error_name} ({status_code}): {error_reason}。解决方法：{solution}"
        else:
            error_msg = f"DeepSeek API 请求失败，未知状态码: {status_code}，响应内容: {response_text}"
        
        logger.error(error_msg)
        return error_msg

    async def chat(self, user_msg, temperature=1.0):
        payload = {
            "model": "deepseek-chat",
            "messages": [{
                "role": "user",
                "content": user_msg
            }],
            "temperature": temperature  # 添加温度参数
        }
        connector = aiohttp.TCPConnector(verify_ssl=False)
        logger.info(f"发送请求到 DeepSeek API，URL: {self.api_url}, Payload: {payload}")
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(self.api_url, headers=self.headers, data=json.dumps(payload)) as response:
                logger.info(f"收到 DeepSeek API 响应，状态码: {response.status}")
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"DeepSeek API 响应内容: {result}")
                    return result['choices'][0]['message']['content']
                else:
                    response_text = await response.text()
                    error_msg = self._handle_http_error(response.status, response_text)
                    raise Exception(error_msg)
                     
    def reason(self, user_msg, stream=False, context=None, temperature=1.0):
        """使用 DeepSeek-R1-0528 模型进行推理的入口方法
        
        Args:
            user_msg: 用户当前消息
            stream: 是否使用流式回复
            context: 对话上下文，格式为包含{'role':角色, 'content':内容}的列表
                     角色可以是'user'或'assistant'
            temperature: 控制生成文本的随机性，范围通常为0.0-2.0，默认1.0
        
        Returns:
            如果stream=True，返回异步生成器；如果stream=False，返回协程对象
        """
        if stream:
            return self.stream_reason(user_msg, context, temperature)
        else:
            return self.non_stream_reason(user_msg, context, temperature)
            
    async def stream_reason(self, user_msg, context=None, temperature=1.0):
        """流式推理（异步生成器）"""
        async for chunk in self._reason_stream(user_msg, context, temperature):
            yield chunk
              
    async def non_stream_reason(self, user_msg, context=None, temperature=1.0):
        """非流式推理"""
        return await self._reason_non_stream(user_msg, context, temperature)
            
    async def _reason_stream(self, user_msg, context=None, temperature=1.0):
        """流式推理（异步生成器）"""
        # 初始化上下文为空列表
        if context is None:
            context = []
            
        # 构造完整的消息列表（上下文 + 当前消息）
        messages = context.copy()
        messages.append({
            "role": "user",
            "content": user_msg
        })
        
        payload = {
            "model": "deepseek-reasoner",  # 指定 R1 模型
            "messages": messages,
            "stream": True,  # 支持流式回复
            "temperature": temperature  # 添加温度参数
        }
        connector = aiohttp.TCPConnector(verify_ssl=False)
        logger.info(f"发送请求到 DeepSeek API (R1模型-流式)，URL: {self.api_url}, Payload: {payload}")
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(self.api_url, headers=self.headers, data=json.dumps(payload)) as response:
                logger.info(f"收到 DeepSeek API (R1模型-流式) 响应，状态码: {response.status}")
                if response.status == 200:
                    async for chunk in self._process_stream(response):
                        yield chunk
                else:
                    response_text = await response.text()
                    error_msg = self._handle_http_error(response.status, response_text)
                    raise Exception(error_msg)
            


                     
    async def _reason_non_stream(self, user_msg, context=None, temperature=1.0):
        """非流式推理"""
        # 初始化上下文为空列表
        if context is None:
            context = []
            
        # 构造完整的消息列表（上下文 + 当前消息）
        messages = context.copy()
        messages.append({
            "role": "user",
            "content": user_msg
        })
        
        payload = {
            "model": "deepseek-reasoner",  # 指定 R1 模型
            "messages": messages,
            "stream": False,  # 非流式回复
            "temperature": temperature  # 添加温度参数
        }
        connector = aiohttp.TCPConnector(verify_ssl=False)
        logger.info(f"发送请求到 DeepSeek API (R1模型-非流式)，URL: {self.api_url}, Payload: {payload}")
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(self.api_url, headers=self.headers, data=json.dumps(payload)) as response:
                logger.info(f"收到 DeepSeek API (R1模型-非流式) 响应，状态码: {response.status}")
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"DeepSeek API (R1模型-非流式) 响应内容: {result}")
                    return result['choices'][0]['message']['content']
                else:
                    response_text = await response.text()
                    error_msg = self._handle_http_error(response.status, response_text)
                    raise Exception(error_msg)
                     
    async def _process_stream(self, response):
        """处理流式响应"""
        async for chunk in response.content:
            if chunk:
                try:
                    chunk_str = chunk.decode('utf-8')
                    # 处理 SSE 格式的响应
                    if chunk_str.startswith('data: '):
                        # 去除 'data: ' 前缀
                        data_str = chunk_str[6:]
                        if data_str.strip() == '[DONE]':
                            break
                        try:
                            data = json.loads(data_str)
                            if 'choices' in data and len(data['choices']) > 0:
                                delta = data['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    yield {'content': delta['content']}
                        except json.JSONDecodeError as e:
                            logger.error(f"解析流式数据JSON错误: {str(e)}, 原始数据: {data_str}")
                except Exception as e:
                    logger.error(f"处理流式数据错误: {str(e)}")

    async def get_balance(self):
        """查询账号余额

        Returns:
            dict: 包含余额信息的字典，格式如下：
                {
                    "is_available": true/false,  // 当前账户是否有余额可供 API 调用
                    "balance_infos": [ ... ]  // 余额详情列表
                }
        """
        connector = aiohttp.TCPConnector(verify_ssl=False)
        logger.info(f"发送余额查询请求到 DeepSeek API，URL: {self.balance_api_url}")
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(self.balance_api_url, headers=self.headers) as response:
                logger.info(f"收到余额查询响应，状态码: {response.status}")
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"余额查询结果: {result}")
                    return result
                else:
                    response_text = await response.text()
                    error_msg = self._handle_http_error(response.status, response_text)
                    raise Exception(error_msg)