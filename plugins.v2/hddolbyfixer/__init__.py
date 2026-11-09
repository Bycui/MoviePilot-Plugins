"""
高清杜比（HDDolby）适配插件。

站点 hddolby.com 已迁移至 uhddolby.com，且杜比官方 API 由 api.{域名}
并入主站 www.{域名}/api/v1。MoviePilot 内置 HDDolby 模块仍固定请求
https://api.{域名}，而内置站点资源中该站登记域名仍为 hddolby.com，
因此站点会出现：搜索被跳过、用户数据无法刷新、站点测试误报「Cookie 已失效」。

本插件在运行时对内置模块打补丁：
1. 搜索接口与用户数据接口统一改为 https://www.{域名}/api/v1/...；
2. 详情页与下载链接按站点实际配置的 url 推导；
3. 若站点域名被写成 uhddolby.com，自动改回 hddolby.com，
   使 MoviePilot 能匹配内置索引器。

插件只修改内存中的类方法，不改动 MoviePilot 源码文件；禁用插件后恢复原始方法。
"""
from typing import Any, Dict, List, Optional, Tuple

from app.db.site_oper import SiteOper
from app.log import logger
from app.modules.indexer.parser.hddolby import HDDolbySiteUserInfo
from app.modules.indexer.spider.hddolby import HddolbySpider
from app.plugins import _PluginBase
from app.utils.string import StringUtils

# 补丁标记，避免重复打补丁
_SPIDER_PATCH_FLAG = "_hddolby_fixer_spider_patched"
_PARSER_PATCH_FLAG = "_hddolby_fixer_parser_patched"
_CHAIN_PATCH_FLAG = "_hddolby_fixer_chain_patched"
_TORRENTS_PATCH_FLAG = "_hddolby_fixer_torrents_patched"
_OPER_PATCH_FLAG = "_hddolby_fixer_oper_patched"

# 原始方法引用，用于禁用插件时回滚
_ORIGINALS: Dict[str, Any] = {}

# 站点新域名与内置索引器域名
_NEW_DOMAIN = "uhddolby.com"
_INDEXER_DOMAIN = "hddolby.com"
_SITE_URL = "https://www.uhddolby.com/"


def _normalize_www_host(value: Optional[str]) -> str:
    """
    把站点地址规范化为 www 主域主机名。

    :param value: 站点地址或域名
    :return: 形如 www.uhddolby.com 的主机名，无法解析时返回空字符串
    """
    host = StringUtils.get_url_domain(value or "")
    if not host:
        return ""
    return host if host.startswith("www.") else f"www.{host}"


def _ensure_trailing_slash(url: str) -> str:
    """
    为站点地址补齐结尾斜杠。

    :param url: 站点地址
    :return: 带结尾斜杠的地址
    """
    if url and not url.endswith("/"):
        return f"{url}/"
    return url


def _is_legacy_site_url(url: Optional[str]) -> bool:
    """
    判断站点地址是否仍指向已失效的旧域名。

    :param url: 站点地址
    :return: 是否属于旧域名
    """
    return StringUtils.get_url_domain(url or "") == _INDEXER_DOMAIN


def _apply_runtime_patches() -> bool:
    """
    对内置杜比模块应用运行时补丁，重复调用保持幂等。

    :return: 是否已成功应用补丁
    """
    if getattr(HddolbySpider, _SPIDER_PATCH_FLAG, False) and getattr(HDDolbySiteUserInfo, _PARSER_PATCH_FLAG, False):
        return True

    if not getattr(HddolbySpider, _SPIDER_PATCH_FLAG, False):
        original_init = HddolbySpider.__init__
        _ORIGINALS["spider_init"] = original_init

        def patched_spider_init(self, indexer: dict):
            """
            构造蜘蛛实例后修正搜索接口与详情页地址。

            :param indexer: 站点索引配置
            """
            original_init(self, indexer)
            if not indexer:
                return
            base_url = _ensure_trailing_slash(str(indexer.get("url") or indexer.get("domain") or ""))
            host = _normalize_www_host(base_url) or _normalize_www_host(str(indexer.get("domain") or ""))
            if base_url:
                self._domain = base_url
                self._pageurl = f"{base_url}details.php?id=%s&hit=1"
            if host:
                self._domain_host = host
                self._searchurl = f"https://{host}/api/v1/torrent/search"

        HddolbySpider.__init__ = patched_spider_init
        setattr(HddolbySpider, _SPIDER_PATCH_FLAG, True)

    if not getattr(HDDolbySiteUserInfo, _PARSER_PATCH_FLAG, False):
        original_parse_site_page = HDDolbySiteUserInfo._parse_site_page
        _ORIGINALS["parser_parse_site_page"] = original_parse_site_page

        def patched_parse_site_page(self, html_text: str):
            """
            修正用户数据接口所在的主域。

            :param html_text: 站点首页内容
            """
            original_parse_site_page(self, html_text)
            host = _normalize_www_host(getattr(self, "_site_url", "")) \
                or _normalize_www_host(getattr(self, "_base_url", ""))
            if host:
                self._base_url = f"https://{host}"

        HDDolbySiteUserInfo._parse_site_page = patched_parse_site_page
        setattr(HDDolbySiteUserInfo, _PARSER_PATCH_FLAG, True)

    if not getattr(SiteOper, _OPER_PATCH_FLAG, False):
        original_update_userdata = SiteOper.update_userdata
        _ORIGINALS["site_oper_update_userdata"] = original_update_userdata

        def patched_update_userdata(self, domain: str, name: str, payload: dict):
            """
            把站点用户数据写入归一后的站点域名，避免写入站点列表中不存在的域名。

            :param domain: 站点域名
            :param name: 站点名称
            :param payload: 用户数据
            :return: 更新结果
            """
            return original_update_userdata(self, _normalize_indexer_domain(domain), name, payload)

        SiteOper.update_userdata = patched_update_userdata
        setattr(SiteOper, _OPER_PATCH_FLAG, True)

    return True


def _patch_site_chain() -> None:
    """让站点连通性测试识别 uhddolby.com 新域名。"""
    try:
        from app.chain.site import SiteChain
    except Exception as err:  # noqa: BLE001
        logger.debug(f"高清杜比适配：跳过站点测试补丁，{err}")
        return

    if getattr(SiteChain, _CHAIN_PATCH_FLAG, False):
        return

    original_init = SiteChain.__init__
    _ORIGINALS["site_chain_init"] = original_init

    def patched_site_chain_init(self):
        """构造站点处理链后补充新域名的专用测试器。"""
        original_init(self)
        hddolby_test = getattr(self, "_SiteChain__hddolby_test", None)
        if hddolby_test and _NEW_DOMAIN not in self.special_site_test:
            self.special_site_test[_NEW_DOMAIN] = hddolby_test

    SiteChain.__init__ = patched_site_chain_init
    setattr(SiteChain, _CHAIN_PATCH_FLAG, True)


def _normalize_indexer_domain(domain: Optional[str]) -> str:
    """
    把杜比新域名归一为内置索引器登记的域名。

    :param domain: 传入的站点域名
    :return: 可被 MoviePilot 索引器匹配的域名
    """
    host = StringUtils.get_url_domain(domain or "")
    if host == _NEW_DOMAIN or host == f"www.{_NEW_DOMAIN}" or host.endswith(f".{_NEW_DOMAIN}"):
        return _INDEXER_DOMAIN
    return domain or ""


def _patch_torrents_chain() -> None:
    """让站点首页浏览与 RSS 刷新把杜比域名归一到内置索引器域名。"""
    try:
        import inspect
        from app.chain.torrents import TorrentsChain
    except Exception as err:  # noqa: BLE001
        logger.debug(f"高清杜比适配：跳过种子链补丁，{err}")
        return

    if getattr(TorrentsChain, _TORRENTS_PATCH_FLAG, False):
        return

    originals: Dict[str, Any] = {}
    for method_name in ("browse", "async_browse", "rss"):
        original = getattr(TorrentsChain, method_name, None)
        if not callable(original):
            continue
        originals[method_name] = original
        if inspect.iscoroutinefunction(original):
            async def async_wrapper(self, domain, *args, __original=original, **kwargs):
                """异步方法：归一域名后调用原始实现。"""
                return await __original(self, _normalize_indexer_domain(domain), *args, **kwargs)

            setattr(TorrentsChain, method_name, async_wrapper)
        else:
            def sync_wrapper(self, domain, *args, __original=original, **kwargs):
                """同步方法：归一域名后调用原始实现。"""
                return __original(self, _normalize_indexer_domain(domain), *args, **kwargs)

            setattr(TorrentsChain, method_name, sync_wrapper)

    _ORIGINALS["torrents_chain"] = originals
    setattr(TorrentsChain, _TORRENTS_PATCH_FLAG, True)


def _revert_runtime_patches() -> None:
    """撤销运行时补丁，恢复 MoviePilot 原始类方法。"""
    if _ORIGINALS.get("spider_init") is not None:
        HddolbySpider.__init__ = _ORIGINALS.pop("spider_init")
        setattr(HddolbySpider, _SPIDER_PATCH_FLAG, False)
    if _ORIGINALS.get("parser_parse_site_page") is not None:
        HDDolbySiteUserInfo._parse_site_page = _ORIGINALS.pop("parser_parse_site_page")
        setattr(HDDolbySiteUserInfo, _PARSER_PATCH_FLAG, False)
    if _ORIGINALS.get("site_chain_init") is not None:
        try:
            from app.chain.site import SiteChain
            SiteChain.__init__ = _ORIGINALS.pop("site_chain_init")
            setattr(SiteChain, _CHAIN_PATCH_FLAG, False)
        except Exception as err:  # noqa: BLE001
            logger.debug(f"高清杜比适配：撤销站点测试补丁失败，{err}")
    if _ORIGINALS.get("torrents_chain"):
        try:
            from app.chain.torrents import TorrentsChain
            for method_name, original in _ORIGINALS.pop("torrents_chain").items():
                setattr(TorrentsChain, method_name, original)
            setattr(TorrentsChain, _TORRENTS_PATCH_FLAG, False)
        except Exception as err:  # noqa: BLE001
            logger.debug(f"高清杜比适配：撤销种子链补丁失败，{err}")
    if _ORIGINALS.get("site_oper_update_userdata") is not None:
        SiteOper.update_userdata = _ORIGINALS.pop("site_oper_update_userdata")
        setattr(SiteOper, _OPER_PATCH_FLAG, False)


class HddolbyFixer(_PluginBase):
    """修复 MoviePilot 内置高清杜比模块的 API 域名适配插件。"""

    # 插件名称
    plugin_name = "高清杜比适配"
    # 插件描述
    plugin_desc = "修复杜比站点迁移域名后无法搜索、无法刷新用户数据、误报 Cookie 失效的问题。"
    # 插件图标
    plugin_icon = "Hddolby.png"
    # 插件版本
    plugin_version = "1.0.4"
    # 插件标签
    plugin_label = "站点管理"
    # 插件作者
    plugin_author = "Bycui"
    # 作者主页
    author_url = ""
    # 插件配置项ID前缀
    plugin_config_prefix = "hddolbyfixer_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 1

    # 插件配置
    _enabled: bool = False
    # 最近一次执行结果
    _last_result: Dict[str, Any] = {}

    def init_plugin(self, config: dict = None) -> None:
        """
        装载运行时补丁，并在需要时修正站点域名。

        :param config: 插件配置字典
        """
        self.stop_service()
        self._enabled = False
        self._last_result = {}

        if not config or not config.get("enabled"):
            self._last_result = {"patched": False, "message": "插件未启用，未应用补丁"}
            self.save_data("last_result", self._last_result)
            return

        self._enabled = True
        patched = _apply_runtime_patches()
        _patch_site_chain()
        _patch_torrents_chain()
        fixed = self.__ensure_site_domain()
        if fixed:
            self.__refresh_sites_cache()

        self._last_result = {
            "patched": patched,
            "fixed_sites": fixed,
            "message": "补丁已生效" + (f"；已修正站点域名：{'、'.join(fixed)}" if fixed else ""),
        }
        self.save_data("last_result", self._last_result)
        logger.info(f"高清杜比适配：{self._last_result['message']}")

    def get_state(self) -> bool:
        """返回插件是否启用。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """本插件不提供远程命令。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """本插件不提供 API。"""
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        返回插件配置表单与默认配置。

        :return: 表单结构列表与默认配置字典
        """
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 4
                                },
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "启用后立即生效，无需重启 MoviePilot。"
                                                    "插件会自动修补内置杜比模块的 API 地址，"
                                                    "并把站点域名改回 hddolby.com 以匹配内置索引器。"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页，展示最近一次补丁执行结果。"""
        result = self.get_data("last_result") or self._last_result or {}
        if not result:
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "text": "插件尚未执行，请先启用。"
                    }
                }
            ]
        fixed = result.get("fixed_sites") or []
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if result.get("patched") else "warning",
                    "variant": "tonal",
                    "text": result.get("message") or "未知状态"
                }
            },
            {
                "component": "VList",
                "props": {
                    "density": "compact"
                },
                "content": [
                    {
                        "component": "VListItem",
                        "props": {
                            "title": "运行时补丁",
                            "subtitle": "已生效" if result.get("patched") else "未生效"
                        }
                    },
                    {
                        "component": "VListItem",
                        "props": {
                            "title": "站点域名修正",
                            "subtitle": "、".join(fixed) if fixed else "无需修正"
                        }
                    },
                    {
                        "component": "VListItem",
                        "props": {
                            "title": "补丁内容",
                            "subtitle": "API 地址改为 https://www.{域名}/api/v1，详情与下载链接按站点 url 推导"
                        }
                    }
                ]
            }
        ]

    def stop_service(self) -> None:
        """禁用或重载插件时撤销运行时补丁。"""
        _revert_runtime_patches()

    def __ensure_site_domain(self) -> List[str]:
        """
        把使用新域名的杜比站点改回内置索引器登记的域名。

        :return: 修正记录列表
        """
        fixed: List[str] = []
        try:
            oper = SiteOper()
            for site in oper.list():
                domain = (site.domain or "").lower()
                if domain == _NEW_DOMAIN:
                    payload: Dict[str, Any] = {"domain": _INDEXER_DOMAIN}
                    if not site.url:
                        payload["url"] = _SITE_URL
                    oper.update(site.id, payload)
                    fixed.append(f"{site.name}(id={site.id}) → {_INDEXER_DOMAIN}")
                    logger.info(f"高清杜比适配：站点 {site.name} 域名 {_NEW_DOMAIN} 修正为 {_INDEXER_DOMAIN}")
                elif domain == _INDEXER_DOMAIN and _is_legacy_site_url(site.url):
                    oper.update(site.id, {"url": _SITE_URL})
                    fixed.append(f"{site.name}(id={site.id}) 站点地址 → {_SITE_URL}")
                    logger.info(f"高清杜比适配：站点 {site.name} 地址修正为 {_SITE_URL}")
        except Exception as err:  # noqa: BLE001
            logger.error(f"高清杜比适配：修正站点域名失败，{err}")
        return fixed

    @staticmethod
    def __refresh_sites_cache() -> None:
        """尝试刷新 MoviePilot 的站点索引器缓存。"""
        try:
            from app.helper.sites import SitesHelper
            helper = SitesHelper()
            for name in ("refresh", "reload", "refresh_indexers", "reinit"):
                func = getattr(helper, name, None)
                if callable(func):
                    func()
                    return
        except Exception as err:  # noqa: BLE001
            logger.debug(f"高清杜比适配：刷新站点缓存失败，{err}")
