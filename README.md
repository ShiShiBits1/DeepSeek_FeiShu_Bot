# 🤖 飞书机器人和Deepseek集成项目

## 📋 项目简介
这是一个集成了飞书(Lark)和Deepseek API的智能机器人项目。该机器人能够接收和处理飞书消息，通过Deepseek API提供智能对话功能，并支持多种指令操作。

## ✨ 功能特点
- 💬 **飞书消息处理**：接收并处理飞书文本消息
- 🧠 **智能对话**：集成Deepseek API，提供AI对话能力
- 📝 **指令系统**：支持余额查询、上下文清除、帮助等指令
- 🗃️ **分布式去重**：使用Redis确保消息不被重复处理
- 🔗 **上下文管理**：维护对话上下文，提供连续对话体验
- 🛡️ **高可用性**：包含重试机制、异常处理和详细日志记录
- ⚙️ **配置灵活**：支持开发/生产环境配置，通过环境变量管理

## 🛠️ 技术栈
- 🐍 Python 3.8+
- 🌐 Flask: Web框架
- 🗄️ Redis: 分布式缓存与去重
- 📨 Lark API: 飞书集成
- 🤖 Deepseek API: AI服务
- 🧩 dotenv: 环境变量管理
- 🚀 Waitress/Gunicorn: 应用服务器

## 📦 项目结构
```
飞书机器人和Deepseek集成项目/
├── app.py              # 主应用文件
├── config_manager.py   # 配置管理
├── deepseek_client.py  # Deepseek API客户端
├── .env                # 环境变量配置
├── requirements.txt    # 依赖包列表
└── logs/               # 日志目录
```

## ⚡ 安装说明
1. ⬇️ 克隆项目到本地
2. 📦 安装依赖包
   ```bash
   pip install -r requirements.txt
   ```
3. 🛠️ 配置环境变量 (复制并修改.env.example为.env)
4. 🗄️ 确保Redis服务已启动
5. ▶️ 运行应用
   ```bash
   python app.py
   ```

## ⚙️ 配置项
在.env文件中配置以下参数：

### 🟢 必须配置
- `DEEPSEEK_API_KEY`: Deepseek API密钥
- `FEISHU_ENCRYPT_KEY`: 飞书加密密钥
- `FEISHU_VERIFICATION_TOKEN`: 飞书验证令牌
- `FEISHU_APP_ID`: 飞书应用ID
- `FEISHU_APP_SECRET`: 飞书应用密钥
- `REDIS_HOST`: Redis主机地址
- `REDIS_PORT`: Redis端口

### 🟡 可选配置
- `ENVIRONMENT`: 运行环境 (development/production，默认development)
- `PORT`: 服务端口 (默认5000)
- `SERVER`: 服务器类型 (waitress/gunicorn，默认waitress)
- `DEEPSEEK_API_URL`: Deepseek API地址 (默认https://api.deepseek.com/v1)

## 🚀 使用方法
### 🏁 启动服务
```bash
# 使用waitress服务器
python app.py

# 或使用gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 📨 飞书指令
在飞书中发送以下指令：
- `/查询余额`: 查询Deepseek API账户余额
- `/清除上下文`: 清除当前对话的上下文历史
- `/帮助` 或 `/help` 或 `/指定`: 查看所有可用指令说明

### 💡 普通对话
直接发送消息即可与机器人进行对话，机器人会自动维护上下文。

## 📦 部署说明
### 🧪 开发环境
1. 确保安装了所有依赖
2. 配置.env文件
3. 运行`python app.py`启动服务

### 🏭 生产环境
1. 确保安装了所有依赖
2. 配置.env文件，设置`ENVIRONMENT=production`
3. 推荐使用Gunicorn或Waitress作为生产服务器
4. 配置反向代理(如Nginx)指向应用服务器
5. 确保Redis服务正常运行

## 📑 日志管理
- 📄 日志文件位于logs/app.log
- 🏭 生产环境日志级别为INFO
- 🧪 开发环境日志级别为DEBUG

## 🚨 异常处理
- ⚠️ 消息处理过程中的异常会被捕获并记录
- 🔄 发送消息失败会自动重试(最多3次)
- 🗂️ 异常事件会记录到Redis，便于后续排查

## 🛠️ 维护说明
1. 🔍 定期检查Redis连接状态
2. 📊 监控API调用量和余额
3. ⏫ 及时更新依赖包版本
4. 🧹 定期清理日志文件

## ⚠️ 注意事项
1. 生产环境下Redis不可用时，应用会自动退出
2. 消息上下文默认保留最近10条记录
3. 事件去重默认有效期为1小时
4. 确保飞书开放平台已正确配置回调地址