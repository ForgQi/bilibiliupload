import random
import re
from urllib.parse import urlencode
import streamlink
import requests

from common.decorators import Plugin
from engine.plugins import BatchCheckBase, match1
from engine.plugins.base_adapter import FFmpegdl

VALID_URL_BASE = r'(?:https?://)?(?:(?:www|go|m)\.)?twitch\.tv/(?P<id>[0-9_a-zA-Z]+)'
_OPERATION_HASHES = {
    'CollectionSideBar': '27111f1b382effad0b6def325caef1909c733fe6a4fbabf54f8d491ef2cf2f14',
    'FilterableVideoTower_Videos': 'a937f1d22e269e39a03b509f65a7490f9fc247d7f83d6ac1421523e3b68042cb',
    'ClipsCards__User': 'b73ad2bfaecfd30a9e6c28fada15bd97032c83ec77a0440766a56fe0bd632777',
    'ChannelCollectionsContent': '07e3691a1bad77a36aba590c351180439a40baefc1c275356f40fc7082419a84',
    'StreamMetadata': '1c719a40e481453e5c48d9bb585d971b8b372f8ebb105b17076722264dfa5b3e',
    'ComscoreStreamingQuery': 'e1edae8122517d013405f237ffcc124515dc6ded82480a88daef69c83b53ac01',
    'VideoPreviewOverlay': '3006e77e51b128d838fa4e835723ca4dc9a05c5efd4466c1085215c6e437e65c',
}
_CLIENT_ID = 'kimne78kx3ncx6brgo4mv6wki5h1ko'


@Plugin.download(regexp=VALID_URL_BASE)
class Twitch(FFmpegdl):
    def __init__(self, fname, url, suffix='flv'):
        FFmpegdl.__init__(self, fname, url, suffix=suffix)

    def check_stream(self):
        if not list(Twitch.BatchCheck([self.url]).check()):
            return
        channel_name = match1(self.url, VALID_URL_BASE)
        # r = requests.get(f'https://api.twitch.tv/api/channels/{channel_name}/access_token',
        #                  headers={
        #                      'Accept': 'application/vnd.twitchtv.v5+json; charset=UTF-8',
        #                      'Client-ID': _CLIENT_ID,
        #                  }, timeout=10)
        # r.close()
        # access_token = r.json()
        # token = access_token['token']
        # query = {
        #     'allow_source': 'true',
        #     'allow_audio_only': 'true',
        #     'allow_spectre': 'true',
        #     'p': random.randint(1000000, 10000000),
        #     'player': 'twitchweb',
        #     'playlist_include_framerate': 'true',
        #     'segment_preference': '4',
        #     'sig': access_token['sig'],
        #     'token': token,
        # }
        self.opt_args = ['-ss', "00:00:20"]
        #
        # r = requests.get( f'https://usher.ttvnw.net/api/channel/hls/{channel_name}.m3u8?{urlencode(query)}')
        # r.close()
        # nums = r.text.split("#")
        # for i in range(len(nums)):
        #     part = re.search(',VIDEO="720p60",', nums[i])
        #     if part :
        #         url = nums[i].split('\n')
        #         real_url = url[-2]

        streams = streamlink.streams(f'https://www.twitch.tv/{channel_name}')
        if channel_name == 'root_supernova':
            stream = streams["720p60"].url
        else:
            stream = streams["best"].url
        # self.raw_stream_url = f'https://usher.ttvnw.net/api/channel/hls/{channel_name}.m3u8?{urlencode(query)}'
        self.raw_stream_url = stream

        return True

    class BatchCheck(BatchCheckBase):
        def __init__(self, urls):
            BatchCheckBase.__init__(self, pattern_id=VALID_URL_BASE, urls=urls)

        def check(self):
            gql = self.get_streamer()
            i = -1
            for data in gql:
                i += 1
                user = data['data'].get('user')
                if not user:
                    continue
                stream = user['stream']
                if not stream:
                    continue
                yield self.usr_dict.get(self.usr_list[i])

        def get_streamer(self):
            ops = []
            for channel_name in self.usr_list:
                op = {
                    'operationName': 'StreamMetadata',
                    'variables': {'channelLogin': channel_name}
                }
                op['extensions'] = {
                    'persistedQuery': {
                        'version': 1,
                        'sha256Hash': _OPERATION_HASHES[op['operationName']],
                    }
                }
                ops.append(op)
            gql = requests.post(
                'https://gql.twitch.tv/gql',
                json=ops,
                headers={
                    'Content-Type': 'text/plain;charset=UTF-8',
                    'Client-ID': _CLIENT_ID,
                }, timeout=15)
            gql.close()
            return gql.json()
