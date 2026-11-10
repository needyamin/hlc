"""Optional MySQL ``SHOW GLOBAL STATUS`` sampler.

Kept behind a flag: in practice ``mysql_series`` was always empty in v1 runs.
An unavailable MySQL sampler must never fail a benchmark; the result labels
the series ``visibility: unavailable`` instead of pretending it is data.
"""

from __future__ import annotations

from .. import docker_probe as dp
from .base import Collector


def loop_script(period=10):
    return dp.mysql_loop_script(period)


class MysqlCollector(Collector):
    name = "mysql"

    def __init__(self, container=""):
        self.container = container
        self.mode = "shell" if container else "none"

    def available(self):
        return bool(self.container)
