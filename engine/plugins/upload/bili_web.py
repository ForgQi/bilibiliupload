import asyncio
import base64
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field, InitVar, asdict
from json import JSONDecodeError
from os.path import basename, splitext
from typing import Any, Union
from urllib import parse
from urllib.parse import quote

import aiohttp
import requests
import rsa
from requests import utils
from requests.adapters import HTTPAdapter, Retry

import engine
from common.decorators import Plugin
from engine.plugins.upload import UploadBase, logger


@Plugin.upload(platform="bili_web")
class BiliWeb(UploadBase):
    def __init__(self, principal, data, user, lines='AUTO', threads=3, tid=174, tags=None, cover_path=None):
        super().__init__(principal, data, persistence_path='engine/bili.cookie')
        if tags is None:
            tags = ['星际争霸2', '电子竞技']
        self.user = user
        self.lines = lines
        self.threads = threads
        self.tid = tid
        self.tags = tags
        self.cover_path = cover_path

    def upload(self, file_list):
        video = Data()
        with BiliBili(video) as bili:
            bili.login(self.persistence_path, self.user)
            for file in file_list:
                video_part = bili.upload_file(file, self.lines, self.threads)  # 上传视频
                video.videos.append(video_part)  # 添加已经上传的视频
            video.title = self.data["format_title"]
            video.desc = '''这个自动录制上传的小程序开源在Github：http://t.cn/RgapTpf(或者在Github搜索ForgQi)
                交流群：837362626'''
            video.source = self.data["url"]  # 添加转载地址说明
            # 设置视频分区,默认为174 生活，其他分区
            video.tid = self.tid
            video.set_tag(self.tags)
            if self.cover_path:
                video.cover = bili.cover_up(self.cover_path).replace('http:', '')
            ret = bili.submit()  # 提交视频
        logger.info(f"上传成功: {ret}")
        self.remove_filelist(file_list)


class BiliBili:
    def __init__(self, video: 'Data'):
        self.app_key = 'bca7e84c2d947ac6'
        self.__session = requests.Session()
        self.video = video
        self.__session.mount('https://', HTTPAdapter(max_retries=Retry(total=5, method_whitelist=False)))
        self.__session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/63.0.3239.108",
            "Referer": "https://www.bilibili.com/", 'Connection': 'keep-alive'
        })
        self.cookies = None
        self.access_token = None
        self.refresh_token = None
        self.__bili_jct = None
        self._auto_os = None
        self.persistence_path = 'engine/bili.cookie'

    def login(self, persistence_path, user):
        self.persistence_path = persistence_path
        if os.path.isfile(persistence_path):
            print('使用持久化内容上传')
            self.load()
        if not self.cookies and user.get('cookies'):
            self.cookies = user['cookies']
        if self.cookies:
            try:
                self.login_by_cookies(self.cookies)
            except:
                logger.exception('login error')
                self.login_by_password(**user['account'])
        else:
            self.login_by_password(**user['account'])
        self.store()

    def load(self):
        try:
            with open(self.persistence_path) as f:
                self.cookies = json.load(f)
                self.access_token = self.cookies['access_token']
        except (JSONDecodeError, KeyError):
            logger.exception('加载cookie出错')

    def store(self):
        with open(self.persistence_path, "w") as f:
            json.dump({**self.cookies,
                       'access_token': self.access_token,
                       'refresh_token': self.refresh_token
                       }, f)

    def login_by_password(self, username, password):
        print('使用账号上传')
        key_hash, pub_key = self.get_key()
        encrypt_password = base64.b64encode(rsa.encrypt(f'{key_hash}{password}'.encode(), pub_key))
        payload = {
            "actionKey": 'appkey',
            "appkey": self.app_key,
            "build": 6040500,
            "captcha": '',
            "challenge": '',
            "channel": 'bili',
            "device": 'phone',
            "mobi_app": 'android',
            "password": encrypt_password,
            "permission": 'ALL',
            "platform": 'android',
            "seccode": "",
            "subid": 1,
            "ts": int(time.time()),
            "username": username,
            "validate": "",
        }
        response = self.__session.post("https://passport.bilibili.com/api/v3/oauth2/login", timeout=5,
                                       data={**payload, 'sign': self.sign(parse.urlencode(payload))})
        r = response.json()
        if r['code'] != 0 and r.get('data') is None:
            raise RuntimeError(r)
        for cookie in r['data']['cookie_info']['cookies']:
            self.__session.cookies.set(cookie['name'], cookie['value'])
            if 'bili_jct' == cookie['name']:
                self.__bili_jct = cookie['value']
        self.cookies = self.__session.cookies.get_dict()
        self.access_token = r['data']['token_info']['access_token']
        self.refresh_token = r['data']['token_info']['refresh_token']
        return r

    def login_by_cookies(self, cookie):
        print('使用cookies上传')
        requests.utils.add_dict_to_cookiejar(self.__session.cookies, cookie)
        if 'bili_jct' in cookie:
            self.__bili_jct = cookie["bili_jct"]

        data = self.__session.get("https://api.bilibili.com/x/web-interface/nav", timeout=5).json()
        if data["code"] != 0:
            raise Exception(data)

    @staticmethod
    def sign(param):
        salt = '60698ba2f68e01ce44738920a0ffe768'
        return hashlib.md5(f"{param}{salt}".encode()).hexdigest()

    def get_key(self):
        url = "https://passport.bilibili.com/api/oauth2/getKey"
        payload = {
            'appkey': f'{self.app_key}',
            'sign': self.sign(f"appkey={self.app_key}"),
        }
        response = self.__session.post(url, data=payload, timeout=5)
        r = response.json()
        if r and r["code"] == 0:
            return r['data']['hash'], rsa.PublicKey.load_pkcs1_openssl_pem(r['data']['key'].encode())

    def probe(self):
        ret = self.__session.get('https://member.bilibili.com/preupload?r=probe', timeout=5).json()
        logger.info(f"线路:{ret['lines']}")
        data, auto_os = None, None
        min_cost = 0
        if ret['probe'].get('get'):
            method = 'get'
        else:
            method = 'post'
            data = bytes(int(1024 * 0.1 * 1024))
        for line in ret['lines']:
            start = time.perf_counter()
            test = self.__session.request(method, f"https:{line['probe_url']}", data=data, timeout=30)
            cost = time.perf_counter() - start
            print(line['query'], cost)
            if test.status_code != 200:
                return
            if not min_cost or min_cost > cost:
                auto_os = line
                min_cost = cost
        auto_os['cost'] = min_cost
        return auto_os

    def upload_file(self, filepath: str, lines='AUTO', tasks=3):
        """上传本地视频文件,返回视频信息dict
        b站目前支持4种上传线路upos, kodo, gcs, bos
        gcs: {"os":"gcs","query":"bucket=bvcupcdngcsus&probe_version=20200810",
        "probe_url":"//storage.googleapis.com/bvcupcdngcsus/OK"},
        bos: {"os":"bos","query":"bucket=bvcupcdnboshb&probe_version=20200810",
        "probe_url":"??"}
        """
        if not self._auto_os:
            self._auto_os = self.probe()
            if lines == 'kodo':
                self._auto_os = {"os": "kodo", "query": "bucket=bvcupcdnkodobm&probe_version=20200810",
                                 "probe_url": "//up-na0.qbox.me/crossdomain.xml"}
            if lines == 'bda2':
                self._auto_os = {"os": "upos", "query": "upcdn=bda2&probe_version=20200810",
                                 "probe_url": "//upos-sz-upcdnbda2.bilivideo.com/OK"}
            if lines == 'ws':
                self._auto_os = {"os": "upos", "query": "upcdn=ws&probe_version=20200810",
                                 "probe_url": "//upos-sz-upcdnws.bilivideo.com/OK"}
            if lines == 'qn':
                self._auto_os = {"os": "upos", "query": "upcdn=qn&probe_version=20200810",
                                 "probe_url": "//upos-sz-upcdnqn.bilivideo.com/OK"}
            logger.info(f"线路选择{self._auto_os['os']}: {self._auto_os['query']}. time: {self._auto_os.get('cost')}")
        if self._auto_os['os'] == 'upos':
            upload = self.upos
        elif self._auto_os['os'] == 'kodo':
            upload = self.kodo
        elif self._auto_os['os'] == "gcs":
            raise NotImplementedError('gcs')
        elif self._auto_os['os'] == "bos":
            raise NotImplementedError('bos')
        else:
            logger.error(f"NoSearch:{self._auto_os['os']}")
            raise NotImplementedError(self._auto_os['os'])
        total_size = os.path.getsize(filepath)
        with open(filepath, 'rb') as f:
            query = {
                'r': self._auto_os['os'],
                'profile': 'ugcupos/bup' if 'upos' == self._auto_os['os'] else "ugcupos/bupfetch",
                'ssl': 0,
                'version': '2.8.12',
                'build': 2081200,
                'name': f.name,
                'size': total_size,
            }
            ret = self.__session.get(
                f"https://member.bilibili.com/preupload?{self._auto_os['query']}", params=query,
                timeout=5)
            return asyncio.run(upload(f, total_size, ret.json(), tasks=tasks))

    async def kodo(self, file, total_size, ret, chunk_size=4194304, tasks=3):
        filename = file.name
        bili_filename = ret['bili_filename']
        key = ret['key']
        endpoint = f"https:{ret['endpoint']}"
        token = ret['uptoken']
        fetch_url = ret['fetch_url']
        fetch_headers = ret['fetch_headers']
        url = f'{endpoint}/mkblk'
        headers = {
            'Authorization': f"UpToken {token}",
        }
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        async def upload_chunk(session, chunks_data, params):
            async with session.post(f'{url}/{len(chunks_data)}',
                                    data=chunks_data, headers=headers) as response:
                end = time.perf_counter() - start
                ctx = await response.json()
                parts.append({"index": params['chunk'], "ctx": ctx['ctx']})
                sys.stdout.write(f"\r{params['end'] / 1000 / 1000 / end:.2f}MB/s "
                                 f"=> {params['partNumber'] / chunks:.1%}")

        start = time.perf_counter()
        await self._upload({}, file, chunk_size, upload_chunk, tasks=tasks)
        cost = time.perf_counter() - start

        logger.info(f'{filename} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s')
        parts.sort(key=lambda x: x['index'])
        self.__session.post(f"{endpoint}/mkfile/{total_size}/key/{base64.urlsafe_b64encode(key.encode()).decode()}",
                            data=','.join(map(lambda x: x['ctx'], parts)), headers=headers, timeout=10)
        r = self.__session.post(f"https:{fetch_url}", headers=fetch_headers, timeout=5).json()
        if r["OK"] != 1:
            raise Exception(r)
        return {"title": splitext(filename)[0], "filename": bili_filename, "desc": ""}

    async def upos(self, file, total_size, ret, tasks=3):
        filename = file.name
        chunk_size = ret['chunk_size']
        auth = ret["auth"]
        endpoint = ret["endpoint"]
        biz_id = ret["biz_id"]
        upos_uri = ret["upos_uri"]
        url = f"https:{endpoint}/{upos_uri.replace('upos://', '')}"  # 视频上传路径
        headers = {
            "X-Upos-Auth": auth
        }
        # 向上传地址申请上传，得到上传id等信息
        upload_id = self.__session.post(f'{url}?uploads&output=json', timeout=5,
                                        headers=headers).json()["upload_id"]
        # 开始上传
        parts = []  # 分块信息
        chunks = math.ceil(total_size / chunk_size)  # 获取分块数量

        async def upload_chunk(session, chunks_data, params):
            async with session.put(url, params=params, raise_for_status=True,
                                   data=chunks_data, headers=headers):
                end = time.perf_counter() - start
                parts.append({"partNumber": params['chunk'] + 1, "eTag": "etag"})
                sys.stdout.write(f"\r{params['end'] / 1000 / 1000 / end:.2f}MB/s "
                                 f"=> {params['partNumber'] / chunks:.1%}")

        start = time.perf_counter()
        await self._upload({
            'uploadId': upload_id,
            'chunks': chunks,
            'total': total_size
        }, file, chunk_size, upload_chunk, tasks=tasks)
        cost = time.perf_counter() - start
        p = {
            'name': filename,
            'uploadId': upload_id,
            'biz_id': biz_id,
            'output': 'json',
            'profile': 'ugcupos/bup'
        }
        r = self.__session.post(url, params=p, json={"parts": parts}, headers=headers, timeout=15).json()
        logger.info(f'{filename} uploaded >> {total_size / 1000 / 1000 / cost:.2f}MB/s. {r}')
        if r.get('OK') != 1:
            raise Exception(r)
        return {"title": splitext(filename)[0], "filename": splitext(basename(upos_uri))[0], "desc": ""}

    @staticmethod
    async def _upload(params, file, chunk_size, afunc, tasks=2):
        params['chunk'] = -1

        async def upload_chunk():
            while True:
                chunks_data = file.read(chunk_size)
                if not chunks_data:
                    return
                params['chunk'] += 1
                params['size'] = len(chunks_data)
                params['partNumber'] = params['chunk'] + 1
                params['start'] = params['chunk'] * chunk_size
                params['end'] = params['start'] + params['size']
                clone = params.copy()
                for i in range(10):
                    try:
                        await afunc(session, chunks_data, clone)
                        break
                    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                        logger.error(f"retry chunk{clone['chunk']} >> {i+1}. {e}")

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*[upload_chunk() for _ in range(tasks)])

    def submit(self):
        if not self.video.title:
            self.video.title = self.video.videos[0]["title"]
        self.__session.get('https://member.bilibili.com/x/geetest/pre/add', timeout=5)
        myinfo = self.__session.get('https://member.bilibili.com/x/web/archive/pre?lang=cn',
                                    timeout=15).json()['data']['myinfo']
        myinfo['total_info'] = self.__session.get('https://member.bilibili.com/x/web/index/stat',
                                                  timeout=15).json()['data']
        user_weight = 2 if myinfo['level'] > 3 \
            and myinfo['total_info'] and myinfo['total_info']['total_fans'] > 100 else 1
        if user_weight == 2:
            logger.info(f'用户权重: {user_weight} => 网页端分p数量不受限制使用网页端api提交')
            ret = self.__session.post(f'https://member.bilibili.com/x/vu/web/add?csrf={self.__bili_jct}', timeout=5,
                                      json=asdict(self.video)).json()
            if ret["code"] == 0:
                return ret
            elif ret["code"] == 21138:
                logger.info(f'改用客户端接口提交{ret}')
            else:
                raise Exception(ret)

        logger.info(f'用户权重: {user_weight} => 网页端分p数量受到限制使用客户端api端提交')
        if not self.access_token:
            self.login_by_password(**engine.config['user']['account'])
            self.store()
        while True:
            ret = self.__session.post(f'http://member.bilibili.com/x/vu/client/add?access_key={self.access_token}',
                                      timeout=5, json=asdict(self.video)).json()
            if ret['code'] == -101:
                logger.info(f'刷新token{ret}')
                self.login_by_password(**engine.config['user']['account'])
                self.store()
                continue
            break

        if ret["code"] == 0:
            return ret
        else:
            raise Exception(ret)

    def cover_up(self, img: str):
        """
        :param img: img path or stream
        :return: img URL
        """
        from PIL import Image
        from io import BytesIO

        with Image.open(img) as im:
            # 宽和高,需要16：10
            xsize, ysize = im.size
            if xsize / ysize > 1.6:
                delta = xsize - ysize * 1.6
                region = im.crop((delta / 2, 0, xsize - delta / 2, ysize))
            else:
                delta = ysize - xsize * 10 / 16
                region = im.crop((0, delta / 2, xsize, ysize - delta / 2))
            buffered = BytesIO()
            region.save(buffered, format=im.format)
        r = self.__session.post(
            url='https://member.bilibili.com/x/vu/web/cover/up',
            data={
                'cover': b'data:image/jpeg;base64,' + (base64.b64encode(buffered.getvalue())),
                'csrf': self.__bili_jct
            }, timeout=30
        )
        buffered.close()
        return r.json()['data']['url']

    def get_tags(self, upvideo, typeid="", desc="", cover="", groupid=1, vfea=""):
        """
        上传视频后获得推荐标签
        :param vfea:
        :param groupid:
        :param typeid:
        :param desc:
        :param cover:
        :param upvideo:
        :return: 返回官方推荐的tag
        """
        url = f'https://member.bilibili.com/x/web/archive/tags?' \
              f'typeid={typeid}&title={quote(upvideo["title"])}&filename=filename&desc={desc}&cover={cover}' \
              f'&groupid={groupid}&vfea={vfea}'
        return self.__session.get(url=url, timeout=5).json()

    def __enter__(self):
        return self

    def __exit__(self, e_t, e_v, t_b):
        self.close()

    def close(self):
        """Closes all adapters and as such the session"""
        self.__session.close()


@dataclass
class Data:
    """
    cover: 封面图片，可由recovers方法得到视频的帧截图
    tid: 分区,174为生活，其他分区
    """
    copyright: int = 2
    source: str = ''
    tid: int = 174
    cover: str = ''
    title: str = ''
    desc_format_id: int = 0
    desc: str = ''
    dynamic: str = ''
    subtitle: dict = field(init=False)
    tag: Union[list, str] = ''
    videos: list = field(default_factory=list)
    dtime: Any = None
    open_subtitle: InitVar[bool] = False

    # interactive: int = 0
    # no_reprint: int 1
    # open_elec: int 1

    def __post_init__(self, open_subtitle):
        self.subtitle = {"open": int(open_subtitle), "lan": ""}
        if self.dtime and self.dtime - int(time.time()) <= 14400:
            self.dtime = None
        if isinstance(self.tag, list):
            self.dynamic = f"#{'##'.join(self.tag)}#"
            self.tag = ','.join(self.tag)

    def delay_time(self, dtime: int):
        """设置延时发布时间，距离提交大于4小时，格式为10位时间戳"""
        if dtime - int(time.time()) > 14400:
            self.dtime = dtime

    def set_tag(self, tag: list):
        """设置标签，tag为数组"""
        self.dynamic = f"#{'##'.join(tag)}#"
        self.tag = ','.join(tag)
