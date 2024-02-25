#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence
from urllib import request


class TTY:
    Reset = '\033[0m'
    Red = '\033[31m'
    Green = '\033[32m'
    Yellow = '\033[33m'
    Blue = '\033[34m'
    White = '\033[97m'

    @staticmethod
    def url(name: str, ref: str) -> str:
        return f'\033]8;;{ref}\033\\{name}\033]8;;\033\\'


class Listable:
    @property
    def package(self) -> str:
        raise NotImplementedError

    @property
    def date(self) -> datetime:
        raise NotImplementedError

    def to_md(self) -> str:
        raise NotImplementedError

    def to_tty(self) -> str:
        raise NotImplementedError


@dataclass
class Build(Listable):
    id: int
    pkg: str
    version: str
    release: str
    ref: str
    tag: str
    tag_url: str
    log_url: str
    status: str
    builder: str
    finished: Optional[str]

    @property
    def package(self) -> str:
        return self.pkg

    @property
    def date(self) -> datetime:
        if self.finished is None:
            return datetime.now(tz=timezone.utc)

        return datetime.fromisoformat(self.finished).astimezone(timezone.utc)

    def to_md(self) -> str:
        return f'[{self.pkg} {self.version}-{self.release}]({self.tag_url})'

    def to_tty(self) -> str:
        return (f'{TTY.Green}{self.pkg}{TTY.Reset} {self.version}-{self.release} ' +
                f'{TTY.Blue}[{self.builder}]{TTY.Reset} ' +
                TTY.url('[🡕]', self.tag_url))


class Builds:
    _url = 'https://build.getsol.us/builds.json'
    _builds: Optional[List[Build]] = None

    @property
    def all(self) -> List[Build]:
        with request.urlopen(self._url) as data:
            return list(json.load(data, object_hook=lambda d: Build(**d)))

    @property
    def packages(self) -> List[Build]:
        return list({b.pkg: b for b in self.all}.values())

    def during(self, start: datetime, end: datetime) -> List[Build]:
        return self._filter(self.all, start, end)

    def updates(self, start: datetime, end: datetime) -> List[Build]:
        return self._filter(self.packages, start, end)

    @staticmethod
    def _filter(builds: List[Build], start: datetime, end: datetime) -> List[Build]:
        return list(filter(lambda b: start <= b.date <= end, builds))


@dataclass
class Commit(Listable):
    ref: str
    ts: str
    msg: str
    author: str

    @staticmethod
    def from_line(line: str) -> 'Commit':
        return Commit(*line.split('\x1e'))

    @property
    def date(self) -> datetime:
        return datetime.fromisoformat(self.ts).astimezone(timezone.utc)

    @property
    def package(self) -> str:
        if ':' not in self.msg:
            return '<unknown>'

        return self.msg.split(':', 2)[0].strip()

    @property
    def change(self) -> str:
        if ':' not in self.msg:
            return self.msg.strip()
        return self.msg.split(':', 2)[1].strip()

    @property
    def url(self) -> str:
        return f'https://github.com/getsolus/packages/commit/{self.ref}'

    def to_md(self) -> str:
        return f'[{self.msg}]({self.url})'

    def to_tty(self) -> str:
        return (f'{TTY.Yellow}{TTY.url(self.ref, self.url)}{TTY.Reset} '
                f'{self.date} '
                f'{TTY.Green}{self.package}: {TTY.Reset}{self.change} '
                f'{TTY.Blue}[{self.author}]{TTY.Reset} ' +
                TTY.url('[🡕]', self.url))


class Git:
    _cmd = ['git', '-c', 'color.ui=always', 'log', '--date=iso-strict', '--no-merges',
            '--reverse', '--pretty=format:%h%x1e%ad%x1e%s%x1e%an']

    def commits_by_pkg(self, start: datetime, end: datetime) -> Dict[str, List[Commit]]:
        commits: Dict[str, List[Commit]] = {}
        for commit in self.commits(start, end):
            commits[commit.package] = commits.get(commit.package, []) + [commit]

        return commits

    def commits(self, start: datetime, end: datetime) -> List[Commit]:
        return [Commit.from_line(line) for line in self._commits(start, end)]

    def _commits(self, start: datetime, end: datetime) -> List[str]:
        out = subprocess.Popen(self._cmd + [f'--after={start}', f'--before={end}'],
                               stdout=subprocess.PIPE, stderr=sys.stderr).stdout
        if out is None:
            return []

        return out.read().decode('utf8').strip().split("\n")


def parse_date(date: str) -> datetime:
    out = subprocess.Popen(['date', '-u', '--iso-8601=s', '--date=' + date],
                           stdout=subprocess.PIPE).stdout
    if out is None:
        raise ValueError(f'invalid date: {repr(date)}')

    return datetime.fromisoformat(out.read().decode('utf8').strip())


class Printer:
    def __init__(self, after: str, before: str):
        self.start = parse_date(after)
        self.end = parse_date(before)
        self.builds = Builds()
        self.git = Git()

    def print(self, kind: str, format: str, sort: bool = False) -> None:
        items = self._items(kind)
        if sort:
            items = sorted(items, key=lambda item: (item.package, item.date))

        print(f'{len(items)} {cli_args.command}:')
        self._print(items, format)

    def follow(self, kind: str, format: str) -> None:
        while True:
            self.end = datetime.now(timezone.utc)
            self._print(self._items(kind), format)
            self.start = self.end
            time.sleep(10)

    def _items(self, kind: str) -> Sequence[Listable]:
        match kind:
            case 'builds':
                return self.builds.during(self.start, self.end)
            case 'updates':
                return self.builds.updates(self.start, self.end)
            case 'commits':
                return self.git.commits(self.start, self.end)
            case _:
                raise ValueError(f'unsupported log kind: {kind}')

    @staticmethod
    def _print(items: Sequence[Listable], fmt: str) -> None:
        match fmt:
            case 'tty':
                lines = [item.to_tty() for item in items]
            case 'md':
                lines = [f'- {item.to_md()}' for item in items]
            case _:
                raise ValueError(f'unsupported format: {fmt}')

        if len(items) > 0:
            print("\n".join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', type=str, choices=['builds', 'updates', 'commits'])
    parser.add_argument('after', type=str, help='Show builds after this date')
    parser.add_argument('before', type=str, nargs='?', default=datetime.now(timezone.utc).isoformat(),
                        help='Show builds before this date. Defaults to `now`.')
    parser.add_argument('--format', '-f', type=str, choices=['md', 'tty'], default='tty')
    parser.add_argument('--sort', '-s', action='store_true', help='Sort packages in lexically ascending order')
    parser.add_argument('--follow', '-F', action='store_true',
                        help='Wait for and output new entries when they are created')

    cli_args = parser.parse_args()
    printer = Printer(cli_args.after, cli_args.before)

    if cli_args.follow:
        printer.follow(cli_args.command, cli_args.format)
    else:
        printer.print(cli_args.command, cli_args.format, cli_args.sort)
