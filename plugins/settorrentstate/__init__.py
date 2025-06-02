import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, List, Dict, Tuple, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from bencode import bdecode, bencode

from app.core.config import settings
from app.helper.torrent import TorrentHelper
from app.log import logger
from app.modules.qbittorrent import Qbittorrent
from app.modules.transmission import Transmission
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.utils.string import StringUtils


class TorrentSet(_PluginBase):
    # 插件名称
    plugin_name = "根据筛选条件操作种子"
    # 插件描述
    plugin_desc = "根据筛选条件（分类、标签）对种子进行操作（限速）"
    # 插件图标
    plugin_icon = "seed.png"
    # 插件版本
    plugin_version = "0.1"
    # 插件作者
    plugin_author = "xiaojiang"
    # 作者主页
    author_url = "https://github.com/zzj9754"
    # 插件配置项ID前缀
    plugin_config_prefix = "torrentSet_"
    # 加载顺序
    plugin_order = 18
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _scheduler = None
    qb = None
    tr = None
    torrent = None
    # 开关
    _enabled = False
    _cron = None
    _onlyonce = False
    _downloader = None
    _todownloader = None
    _frompath = None
    _topath = None
    _notify = False
    _nolabels = None
    _includelabels = None
    _includecategory = None
    _nopaths = None
    _deletesource = False
    _deleteduplicate = False
    torrentpath = None
    _autostart = False
    _transferemptylabel = False
    _add_torrent_tags = None
    # 退出事件
    _event = Event()
    # 待检查种子清单
    _recheck_torrents = {}
    _is_recheck_running = False
    # 任务标签
    _torrent_tags = []

    def init_plugin(self, config: dict = None):
        self.torrent = TorrentHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._notify = config.get("notify")
            self._nolabels = config.get("nolabels") 
            self._includelabels = config.get("includelabels")
            self._includecategory = config.get("includecategory")
            self._frompath = config.get("frompath")
            self._topath = config.get("topath")
            self._downloader = config.get("downloader")
            self._todownloader = config.get("todownloader")
            self._deletesource = config.get("deletesource")
            self._deleteduplicate = config.get("deleteduplicate")
            self.torrentpath = config.get("fromtorrentpath")
            self._nopaths = config.get("nopaths")
            self._transferemptylabel = config.get("transferemptylabel")
            self._add_torrent_tags = config.get("add_torrent_tags")
            self._torrent_tags = self._add_torrent_tags.strip().split(",") if self._add_torrent_tags else []

        # 停止现有任务
        self.stop_service()

        # 启动定时任务 & 立即运行一次
        if self.get_state() or self._onlyonce:
            self.qb = Qbittorrent()
            self.tr = Transmission()

            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._onlyonce:
                logger.info(f"转移做种服务启动，立即运行一次")
                self._scheduler.add_job(self.transfer, 'date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                            seconds=3))
                # 关闭一次性开关
                self._onlyonce = False
                config["onlyonce"] = self._onlyonce
                self.update_config(config=config)

            # 启动服务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self):
        return True if self._enabled \
                       and self._cron \
                       and self._fromdownloader \
                       and self._todownloader \
                       and self.torrentpath else False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self.get_state():
            return [
                {
                    "id": "TorrentTransfer",
                    "name": "转移做种服务",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.transfer,
                    "kwargs": {}
                }
            ]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '0 0 0 ? *'
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'downloader',
                                            'label': '下载器',
                                            'items': [
                                                {'title': 'Qbittorrent', 'value': 'qbittorrent'},
                                                {'title': 'Transmission', 'value': 'transmission'}
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'nopaths',
                                            'label': '不转移数据文件目录',
                                            'rows': 3,
                                            'placeholder': '标签|下载速度|上传速度'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                ]
            }
        ], {
            "enabled": False,
            "notify": False,
            "onlyonce": False,
            "cron": "",
            "nolabels": "",
            "includelabels": "",
            "includecategory": "",
            "frompath": "",
            "topath": "",
            "fromdownloader": "",
            "todownloader": "",
            "deletesource": False,
            "deleteduplicate": False,
            "fromtorrentpath": "",
            "nopaths": "",
            "autostart": True,
            "transferemptylabel": False,
            "add_torrent_tags": "已整理,转移做种"
        }

    def get_page(self) -> List[dict]:
        pass

    def __get_downloader(self, dtype: str):
        """
        根据类型返回下载器实例
        """
        if dtype == "qbittorrent":
            return self.qb
        elif dtype == "transmission":
            return self.tr
        else:
            return None

    def __download(self, downloader: str, content: bytes,
                   save_path: str) -> Optional[str]:
        """
        添加下载任务
        """
        if downloader == "qbittorrent":
            # 生成随机Tag
            tag = StringUtils.generate_random_str(10)
            state = self.qb.add_torrent(content=content,
                                        download_dir=save_path,
                                        is_paused=True,
                                        tag=self._torrent_tags + [tag])
            if not state:
                return None
            else:
                # 获取种子Hash
                torrent_hash = self.qb.get_torrent_id_by_tag(tags=tag)
                if not torrent_hash:
                    logger.error(f"{downloader} 下载任务添加成功，但获取任务信息失败！")
                    return None
            return torrent_hash
        elif downloader == "transmission":
            # 添加任务
            torrent = self.tr.add_torrent(content=content,
                                          download_dir=save_path,
                                          is_paused=True,
                                          labels=self._torrent_tags)
            if not torrent:
                return None
            else:
                return torrent.hashString

        logger.error(f"不支持的下载器：{downloader}")
        return None

    def set_state(self):
        """
        开始转移做种
        """
        logger.info("开始设置状态 ...")

        # 下载器
        downloader = self._downloader

        # 获取下载器中已完成的种子
        downloader_obj = self.__get_downloader(downloader)
        torrents = downloader_obj.get_completed_torrents()
        if torrents:
            logger.info(f"下载器 {downloader} 已完成种子数：{len(torrents)}")
        else:
            logger.info(f"下载器 {downloader} 没有已完成种子")
            return

        # 开始根据种子筛选条件设置种子状态
        for torrent in torrents:
            if self._event.is_set():
                logger.info(f"服务停止")
                return

            # 获取种子标签
            torrent_labels = self.__get_label(torrent, downloader)
            # 种子为无标签,退出
            is_torrent_labels_empty = ((torrent_labels == ['']) or (torrent_labels == []) or (torrent_labels is None))
            if is_torrent_labels_empty:
                torrent_labels = []
                continue




    @staticmethod
    def __get_label(torrent: Any, dl_type: str):
        """
        获取种子标签
        """
        try:
            return [str(tag).strip() for tag in torrent.get("tags").split(',')] \
                if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            print(str(e))
            return []

    @staticmethod
    def __get_category(torrent: Any, dl_type: str):
        """
        获取种子分类
        """
        try:
            return torrent.get("category").strip() \
                if dl_type == "qbittorrent" else ""
        except Exception as e:
            print(str(e))
            return ""


    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))
