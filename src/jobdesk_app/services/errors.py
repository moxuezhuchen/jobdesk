"""服务层异常定义。"""


class ServiceError(Exception):
    """服务层通用异常。"""


class ConfigNotFoundError(ServiceError):
    """配置文件未找到。"""


class ServerNotFoundError(ServiceError):
    """引用的 server_id 不存在。"""


class InputDiscoveryError(ServiceError):
    """输入发现阶段错误。"""
