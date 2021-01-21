#!/usr/bin/python3
# coding:utf8
import asyncio
import sys
import common
from engine import main
from common.Daemon import Daemon

if __name__ == '__main__':

    sys.excepthook = common.new_hook

    daemon = Daemon('watch_process.pid')
    if len(sys.argv) == 2:
        if 'start' == sys.argv[1]:
            daemon.start()
        elif 'stop' == sys.argv[1]:
            daemon.stop()
        elif 'restart' == sys.argv[1]:
            daemon.restart()
        else:
            print('unknown command')
            sys.exit(2)
        sys.exit(0)
    elif len(sys.argv) == 1:
        asyncio.run(main())
    else:
        print('usage: %s start|stop|restart' % sys.argv[0])
        sys.exit(2)
