import random
import base64
from itertools import cycle
from urllib.parse import urlparse
from scrapy import signals
from proxyservice_wc.api import ProxyServiceAPI
from twisted.internet.error import TimeoutError as ServerTimeoutError
from twisted.internet.error import ConnectionRefusedError
from twisted.internet.error import ConnectionDone
from twisted.internet.error import ConnectError
from twisted.internet.error import ConnectionLost
from twisted.internet.error import TCPTimedOutError
from twisted.internet.defer import TimeoutError as UserTimeoutError


def extract_auth_from_url(url):
    url_parts = urlparse(url)
    new_url = ('{}://{}'
               .format(url_parts.scheme,
                       url_parts.host))
    if url_parts.port is not None:
        new_url += ':{}'.format(url_parts.port)
    return (url_parts.username, url_parts.password, new_url)


class ProxyServiceMiddlewareError(Exception):
    pass


class ProxyServiceMiddleware(object):
    def __init__(self, crawler):
        self.crawler = crawler

        self._api_auth = self._get_api_auth_from_settings()
        self._proxy_service_api = ProxyServiceAPI(**self._api_auth)

        self.use_proxies = set()
        self.proxy_list = {}
        self.blocked_http_codes = [503, 403, 504]
        self.blocked_exceptions = (
            ServerTimeoutError, UserTimeoutError,
            ConnectionRefusedError, ConnectionDone, ConnectError,
            ConnectionLost, TCPTimedOutError, IOError)

    @classmethod
    def from_crawler(cls, crawler):
        o = cls(crawler)
        crawler.signals.connect(o.spider_opened, signals.spider_opened)
        crawler.signals.connect(o.spider_closed, signals.spider_closed)
        return o

    def _get_api_auth_from_settings(self):
        try:
            auth = {
                'host': self.crawler.settings.get('PROXY_SERVICE_HOST', ''),
                'user': self.crawler.settings.get('PROXY_SERVICE_USER', ''),
                'password': self.crawler.settings.get('PROXY_SERVICE_PSWD', ''),
            }
        except Exception as e:
            msg = ('You must set the API host, user and password'
                   ': ERROR found => {!r}'.format(e))
            raise ProxyServiceMiddlewareError(msg)
        return auth

    def _next_proxy(self, spider, try_load=True):
        try:
            algorithm = (getattr(spider, 'proxy_service_algorithm', None)
                         or 'random')
            if algorithm == 'random':
                next_proxy = random.choice(
                    self.proxy_list[spider.proxy_service_target_id])
            else:
                plist = self.proxy_list[spider.proxy_service_target_id]
                next_proxy = plist.next()
        except:
            if try_load is True:
                self._load_proxy_list(spider)
                return self._next_proxy(spider, try_load=False)
            else:
                return None, None
        return next_proxy['id'], next_proxy['url']

    def _load_proxy_list(self, spider, blocked=None):
        target_id = spider.proxy_service_target_id
        should_load_proxies = (
            (target_id not in self.proxy_list) or (blocked is not None) or
            (target_id in self.proxy_list and not self.proxy_list[target_id]))
        if should_load_proxies:
            length = getattr(spider, 'proxy_service_length', 10) or 10
            profile = getattr(spider, 'proxy_service_profile', None)
            locations = getattr(spider, 'proxy_service_locations', '')
            types = getattr(spider, 'proxy_service_types', '')
            providers = getattr(spider, 'proxy_service_providers', '')
            proxy_list = self._proxy_service_api.get_proxy_list(
                target_id, length=length, profile=profile, locations=locations,
                types=types, providers=providers, blocked=blocked,
                log=spider.log)

            algorithm = (getattr(spider, 'proxy_service_algorithm', None)
                         or 'random')
            if algorithm == 'random':
                self.proxy_list[target_id] = list(proxy_list)
            else:
                self.proxy_list[target_id] = cycle(proxy_list)

    def _is_blocked_response(self, response, spider):
        callback = None
        if hasattr(spider, 'proxy_service_check_response'):
            if callable(spider.proxy_service_check_response):
                callback = spider.proxy_service_check_response
        if response.status in self.blocked_http_codes:
            return True
        elif callback is not None:
            return callback(response)
        return False

    def _replace_proxy(self, request, spider):
        proxy_id, proxy_url = self._next_proxy(spider)
        if not proxy_id and not proxy_url:
            spider.log('>>> PROXY SERVICE ERROR: "next proxy not found"')
        else:
            # extract user and password from url
            user, password, new_url = extract_auth_from_url(proxy_url)
            request.meta['proxy'] = new_url
            request.meta['proxy_id'] = proxy_id
            # add HTTP authorization
            if user:
                authstr = (base64.b64encode('{user}:{pswd}'
                                            .format(user=user,
                                                    pswd=password)))
                request.headers['Proxy-Authorization'] = 'Basic ' + authstr
            spider.log('Processing request to {} using proxy {}'
                       .format(request.url, request.meta['proxy']))

    def spider_opened(self, spider):
        if hasattr(spider, 'proxy_service_target_id'):
            self.use_proxies.add(spider.name)
            self._load_proxy_list(spider)

    def spider_closed(self, spider):
        if spider.name in self.use_proxies:
            self.use_proxies.remove(spider.name)

    def process_request(self, request, spider):
        disabled = (request.meta.get('proxy_service_disabled', None) or False)
        if (spider.name in self.use_proxies) and (not disabled):
            self._replace_proxy(request, spider)

    def process_response(self, request, response, spider):
        disabled = (request.meta.get('proxy_service_disabled', None) or False)
        if (spider.name in self.use_proxies) and (not disabled):
            mark_as_blocked = (
                self._is_blocked_response(response, spider) and
                'proxy_id' in request.meta)
            if mark_as_blocked:
                self._load_proxy_list(spider,
                                      blocked=[int(request.meta['proxy_id'])])
        return response

    def process_exception(self, request, exception, spider):
        if spider.name in self.use_proxies:
            mark_as_blocked = (
                isinstance(exception, self.blocked_exceptions) and
                'proxy_id' in request.meta)
            if mark_as_blocked:
                self._load_proxy_list(spider,
                                      blocked=[int(request.meta['proxy_id'])])
                self._replace_proxy(request, spider)
