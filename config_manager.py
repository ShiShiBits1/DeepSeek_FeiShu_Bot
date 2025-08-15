import os
import logging
from dotenv import load_dotenv

class ConfigManager:
    def __init__(self):
        # 加载环境变量
        load_dotenv()
        # 确定当前环境
        self.env = os.getenv('ENVIRONMENT', 'development').lower()
        logging.info(f'当前运行环境: {self.env}')

    def get(self, key, default=None):
        return os.getenv(key, default)

    def is_production(self):
        """检查是否为生产环境"""
        return self.env == 'production'

    def get_log_level(self):
        """根据环境获取日志级别"""
        if self.is_production():
            return logging.INFO
        else:
            return logging.DEBUG