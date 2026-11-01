#!/usr/bin/env python3
"""Approved local delivery bytes, declarations and integrity. No upload/extraction.

MIT License; Copyright (c) 2026 Michael Jabbour. See LICENSE.
POSIX descriptor-relative no-symlink operations are required; no unsafe fallback.
"""
import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import uuid
import zipfile

__version__ = '0.1.0'
MANIFEST = 'DELIVERY-MANIFEST.json'
MAX_METADATA = 256 * 1024
CHUNK = 1024 * 1024
MEANING = 'Byte integrity and author declarations only; not ownership, accessibility or delivery proof.'

class DeliveryError(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise DeliveryError(message)

@dataclass(frozen=True)
class Limits:
    max_files: int = 128
    max_bytes: int = 64 * 1024 * 1024
    def __post_init__(self):
        require(type(self.max_files) is int and 1 <= self.max_files <= 128, 'File limit must be 1–128')
        require(type(self.max_bytes) is int and 1 <= self.max_bytes <= 512 * 1024 * 1024, 'Byte limit must be 1–512 MiB')

def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def decode(data):
    require(len(data) <= MAX_METADATA, 'Metadata exceeds 256 KiB')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, 'Duplicate JSON key')
            out[key] = value
        return out
    def constant(_):
        raise DeliveryError('Non-finite JSON number refused')
    return json.loads(data, object_pairs_hook=unique, parse_constant=constant)

def text(value, label):
    require(isinstance(value, str) and value.strip() and len(value) <= 2000, label + ' must be nonempty text, at most 2000 characters')
    return value

def strings(value, label, empty=True):
    require(isinstance(value, list) and len(value) <= 128 and (empty or value), label + ' must be a bounded list')
    for item in value:
        text(item, label)

def path_name(value):
    text(value, 'Asset path')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value), 'Control character in path')
    require(not any(c in value for c in '\\:*?[]'), 'Only literal portable relative file paths are supported')
    path = PurePosixPath(value)
    require(not path.is_absolute() and value == path.as_posix() and len(path.parts) <= 32, 'Noncanonical or absolute path')
    require(all(p not in {'.', '..', '', '.git', '.research', '__pycache__'} and not p.startswith('.env') for p in path.parts), 'Traversal or private-working path refused')
    return path

def validate_plan(plan, limits=Limits()):
    keys = {'schema_version', 'title', 'owner', 'approval_reference', 'destination', 'next_action', 'unresolved_limits', 'assets'}
    require(type(plan) is dict and set(plan) == keys, 'Plan has missing or unknown fields')
    require(plan['schema_version'] == 'delivery-plan/v1', 'Unsupported plan version')
    for key in ['title', 'owner', 'approval_reference', 'next_action']:
        text(plan[key], key)
    destination = plan['destination']
    require(type(destination) is dict and set(destination) == {'label', 'status', 'evidence_ref'}, 'Invalid destination declaration')
    text(destination['label'], 'Destination label')
    require(destination['status'] in {'not_sent', 'reported_sent', 'reported_received', 'unknown'}, 'Unknown declared destination status')
    if destination['status'] in {'reported_sent', 'reported_received'}:
        text(destination['evidence_ref'], 'Reported status evidence reference')
    else:
        require(destination['evidence_ref'] is None, 'No claimed delivery evidence for unobserved status')
    strings(plan['unresolved_limits'], 'Unresolved limits')
    assets = plan['assets']
    require(isinstance(assets, list) and 1 <= len(assets) <= limits.max_files, 'Explicit allowlist needs 1–file-limit assets')
    names = set()
    for item in assets:
        require(type(item) is dict and set(item) == {'path', 'role', 'credits', 'license', 'accessibility'}, 'Invalid asset declaration')
        path_name(item['path'])
        key = item['path'].casefold()
        require(key not in names, 'Duplicate or case-colliding asset path')
        names.add(key)
        require(item['role'] in {'editable', 'final'}, 'Asset role must be editable or final')
        strings(item['credits'], 'Credits', empty=False)
        text(item['license'], 'License declaration')
        text(item['accessibility'], 'Accessibility declaration')
    require(len(encoded(plan)) <= MAX_METADATA, 'Plan metadata exceeds limit')
    return plan

def _flags(directory=False):
    require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'Supported POSIX no-follow operations are required')
    return os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)

def _directory(path):
    raw = Path(path).absolute()
    require(not raw.is_symlink(), 'Declared root cannot be a symlink')
    canonical = raw.resolve(strict=True)
    fd = os.open('/', _flags(True))
    try:
        for component in canonical.parts[1:]:
            nxt = os.open(component, _flags(True), dir_fd=fd)
            os.close(fd); fd = nxt
        return fd, canonical
    except BaseException:
        os.close(fd); raise

def _file(rootfd, relative):
    path = path_name(relative)
    directory = os.dup(rootfd)
    try:
        for component in path.parts[:-1]:
            nxt = os.open(component, _flags(True), dir_fd=directory)
            os.close(directory); directory = nxt
        fd = os.open(path.name, _flags(), dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd); raise DeliveryError('Only regular asset files are supported')
        return fd
    finally:
        os.close(directory)

def _read(fd, remaining, consume=None):
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and before.st_size <= remaining, 'Nonregular file or byte budget exceeded')
    total, sha = 0, hashlib.sha256()
    while True:
        chunk = os.read(fd, min(CHUNK, remaining - total + 1))
        if not chunk:
            break
        total += len(chunk)
        require(total <= remaining, 'Byte budget exceeded while reading')
        sha.update(chunk)
        if consume:
            consume(chunk)
    after = os.fstat(fd)
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Asset changed during read')
    require(total == after.st_size, 'Incomplete asset read')
    return total, sha.hexdigest()

def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

def _index_preflight(stream, size, limits):
    """Bound the supported single-disk, no-comment/no-extra ZIP index before parsing."""
    require(size >= 22, 'Missing ZIP end record')
    stream.seek(size - 22)
    end = stream.read(22)
    require(len(end) == 22, 'Incomplete ZIP end record')
    signature, disk, central_disk, disk_count, count, central_size, offset, comment = struct.unpack('<4s4H2LH', end)
    require(signature == b'PK\x05\x06' and disk == central_disk == comment == 0, 'Unsupported ZIP envelope')
    require(1 < count == disk_count <= limits.max_files + 1, 'ZIP index count exceeds bound')
    require(offset + central_size == size - 22, 'Invalid ZIP central directory extent')
    stream.seek(offset)
    consumed = 0
    for _ in range(count):
        raw = stream.read(46)
        require(len(raw) == 46 and raw[:4] == b'PK\x01\x02', 'Invalid ZIP central record')
        name_size, extra_size, comment_size, start_disk = struct.unpack_from('<4H', raw, 28)
        require(0 < name_size <= 4 * 2000 + len('assets/') and extra_size == comment_size == start_disk == 0, 'Unsupported ZIP index metadata')
        consumed += 46 + name_size
        require(consumed <= central_size, 'ZIP central record exceeds bound')
        require(len(stream.read(name_size)) == name_size, 'Incomplete ZIP filename')
    require(consumed == central_size, 'ZIP member count differs from bounded index')
    stream.seek(0)

def _exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False); return True
    except FileNotFoundError:
        return False

def _prepare(root, plan, destination, name, limits, stack):
    validate_plan(plan, limits)
    require(isinstance(name, str) and path_name(name).name == name and name.lower().endswith('.zip'), 'Archive name must be one relative .zip filename')
    rootfd, realroot = _directory(root); stack.callback(os.close, rootfd)
    destfd, realdest = _directory(destination); stack.callback(os.close, destfd)
    require(not _exists(destfd, name), 'Destination already exists; nothing overwritten')
    rows, total = [], 0
    for item in sorted(plan['assets'], key=lambda a: a['path']):
        fd = _file(rootfd, item['path'])
        try:
            size, sha = _read(fd, limits.max_bytes - total)
        finally:
            os.close(fd)
        total += size
        rows.append({**item, 'bytes': size, 'sha256': sha})
    content = {'plan': plan, 'files': rows}
    manifest = {'schema_version': 'delivery-package/v1', **content, 'content_sha256': digest(encoded(content)),
                'meaning': MEANING}
    require(len(encoded(manifest)) <= MAX_METADATA, 'Manifest metadata exceeds limit')
    binding = {'content_sha256': manifest['content_sha256'], 'source_root': str(realroot), 'destination_root': str(realdest),
               'source_identity': [os.fstat(rootfd).st_dev, os.fstat(rootfd).st_ino],
               'destination_identity': [os.fstat(destfd).st_dev, os.fstat(destfd).st_ino], 'archive_name': name,
               'limits': {'max_files': limits.max_files, 'max_bytes': limits.max_bytes}}
    return rootfd, destfd, manifest, digest(encoded(binding))

def preview(root, plan, destination, name, limits=Limits()):
    with ExitStack() as stack:
        _, _, manifest, approval = _prepare(root, plan, destination, name, limits, stack)
        return {'status': 'preview', 'approval_sha256': approval, 'manifest': manifest,
                'destination': str(Path(destination).resolve() / name), 'writes': False,
                'authority': 'The operator must independently approve the exact files and destination. Metadata is not permission.'}

def _info(name):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info

def build(root, plan, destination, name, expected_plan_sha256, limits=Limits()):
    require(isinstance(expected_plan_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_plan_sha256), 'Explicit reviewed preview SHA-256 required')
    cleanup, result = [], None
    with ExitStack() as stack:
        rootfd, destfd, manifest, approval = _prepare(root, plan, destination, name, limits, stack)
        require(approval == expected_plan_sha256, 'Preview changed: inspect and approve a new preview')
        stage = '.delivery-packager-' + uuid.uuid4().hex
        os.mkdir(stage, 0o700, dir_fd=destfd)
        stagefd = os.open(stage, _flags(True), dir_fd=destfd)
        try:
            fd = os.open('archive.zip', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stagefd)
            with os.fdopen(fd, 'w+b') as output:
                with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
                    for row in manifest['files']:
                        assetfd = _file(rootfd, row['path'])
                        try:
                            with archive.open(_info('assets/' + row['path']), 'w') as entry:
                                size, sha = _read(assetfd, limits.max_bytes, entry.write)
                            require((size, sha) == (row['bytes'], row['sha256']), 'Asset changed after preview')
                        finally:
                            os.close(assetfd)
                    archive.writestr(_info(MANIFEST), encoded(manifest))
                output.flush(); os.fsync(output.fileno()); output.seek(0)
                archive_sha = hashlib.file_digest(output, 'sha256').hexdigest()
                archive_bytes = output.seek(0, os.SEEK_END)
            os.link('archive.zip', name, src_dir_fd=stagefd, dst_dir_fd=destfd, follow_symlinks=False)
            result = {'status': 'created', 'archive_name': name, 'bytes': archive_bytes, 'sha256': archive_sha,
                      'content_sha256': manifest['content_sha256'], 'approval_sha256': approval,
                      'files': len(manifest['files']), 'outbound': 'none', 'destination_delivery': 'not_performed',
                      'declarations_verified': False}
        finally:
            try:
                os.unlink('archive.zip', dir_fd=stagefd)
            except FileNotFoundError:
                pass
            except OSError:
                cleanup.append('staged archive cleanup incomplete')
            os.close(stagefd)
            try:
                os.rmdir(stage, dir_fd=destfd)
            except OSError:
                cleanup.append('private staging directory cleanup incomplete')
        result['cleanup_warnings'] = cleanup
        return result

def verify(archive_path, expected_sha256=None, limits=Limits()):
    path = Path(archive_path)
    parentfd, _ = _directory(path.parent)
    try:
        fd = _file(parentfd, path.name)
    finally:
        os.close(parentfd)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        # Each validated path has at most2000 Unicode characters (at most4 UTF-8
        # bytes each). ZIP stores names in both local and central headers.
        overhead = 22 + (limits.max_files + 1) * (76 + 2 * (4 * 2000 + len('assets/')))
        require(before.st_size <= limits.max_bytes + MAX_METADATA + overhead, 'Archive exceeds byte bound')
        sha = hashlib.file_digest(stream, 'sha256').hexdigest(); stream.seek(0)
        if expected_sha256 is not None:
            require(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256) and sha == expected_sha256, 'Archive SHA-256 mismatch')
        _index_preflight(stream, before.st_size, limits)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist(); names = [i.filename for i in infos]
            require(1 < len(names) <= limits.max_files + 1 and len(set(names)) == len(names), 'Missing, duplicate or excessive ZIP members')
            require(MANIFEST in names, 'No delivery manifest')
            for info in infos:
                require(not info.is_dir() and info.compress_type == zipfile.ZIP_STORED and not info.flag_bits & 1, 'Unsupported ZIP member type/compression/encryption')
                require(stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG, 'Only regular ZIP members supported')
                require(0 <= info.file_size <= (MAX_METADATA if info.filename == MANIFEST else limits.max_bytes), 'ZIP member byte bound exceeded')
            require(archive.getinfo(MANIFEST).file_size <= MAX_METADATA, 'Manifest metadata exceeds limit')
            manifest = decode(archive.read(MANIFEST))
            require(type(manifest) is dict and set(manifest) == {'schema_version', 'plan', 'files', 'content_sha256', 'meaning'}, 'Invalid manifest fields')
            require(manifest['schema_version'] == 'delivery-package/v1', 'Unsupported manifest version')
            require(manifest['meaning'] == MEANING, 'Manifest must preserve the integrity/declaration boundary')
            validate_plan(manifest['plan'], limits)
            rows = manifest['files']; require(isinstance(rows, list) and len(rows) == len(manifest['plan']['assets']), 'File inventory mismatch')
            bypath = {item['path']: item for item in manifest['plan']['assets']}
            require(set(names) == {MANIFEST} | {'assets/' + p for p in bypath}, 'Extra, omitted or unsafe ZIP member')
            total, seen = 0, set()
            for row in rows:
                require(type(row) is dict and set(row) == {'path', 'role', 'credits', 'license', 'accessibility', 'bytes', 'sha256'}, 'Invalid file record')
                path_name(row['path'])
                require(row['path'] in bypath and row['path'] not in seen, 'Unknown/duplicate file record'); seen.add(row['path'])
                require({k: row[k] for k in bypath[row['path']]} == bypath[row['path']], 'Declaration mismatch')
                require(type(row['bytes']) is int and row['bytes'] >= 0 and isinstance(row['sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['sha256']), 'Invalid file digest/length')
                require(archive.getinfo('assets/' + row['path']).file_size == row['bytes'], 'Declared length mismatch')
                total += row['bytes']; require(total <= limits.max_bytes, 'Total byte bound exceeded')
                h = hashlib.sha256()
                with archive.open('assets/' + row['path']) as content:
                    for chunk in iter(lambda: content.read(CHUNK), b''):
                        h.update(chunk)
                require(h.hexdigest() == row['sha256'], 'Payload SHA-256 mismatch')
            require(manifest['content_sha256'] == digest(encoded({'plan': manifest['plan'], 'files': rows})), 'Manifest content digest mismatch')
        require(_stamp(before) == _stamp(os.fstat(stream.fileno())), 'Archive changed during verification')
    return {'status': 'verified', 'files': len(rows), 'sha256': sha, 'scope': 'Byte integrity and declared metadata, not authenticity, rights or accessibility', 'writes': False}

def load_plan(path):
    path = Path(path)
    directory, _ = _directory(path.parent)
    try:
        fd = _file(directory, path.name)
        with os.fdopen(fd, 'rb') as stream:
            require(os.fstat(stream.fileno()).st_size <= MAX_METADATA, 'Plan file exceeds metadata bound')
            return decode(stream.read(MAX_METADATA + 1))
    finally:
        os.close(directory)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['preview', 'build']:
        p = commands.add_parser(name)
        p.add_argument('--root', required=True); p.add_argument('--plan', required=True)
        p.add_argument('--destination', required=True); p.add_argument('--name', required=True)
        p.add_argument('--max-total-bytes', type=int, default=64 * 1024 * 1024)
        if name == 'build':
            p.add_argument('--apply', action='store_true', required=True)
            p.add_argument('--expect-plan', required=True)
    p = commands.add_parser('verify'); p.add_argument('archive'); p.add_argument('--expected-sha256')
    p.add_argument('--max-total-bytes', type=int, default=64 * 1024 * 1024)
    args = parser.parse_args(argv)
    try:
        limits = Limits(max_bytes=args.max_total_bytes)
        if args.command == 'verify':
            result = verify(args.archive, args.expected_sha256, limits)
        else:
            plan = load_plan(args.plan)
            result = preview(args.root, plan, args.destination, args.name, limits) if args.command == 'preview' else build(args.root, plan, args.destination, args.name, args.expect_plan, limits)
        print(json.dumps(result, indent=2)); return 0
    except (DeliveryError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error), 'outbound': 'none'})); return 2

if __name__ == '__main__':
    sys.exit(main())
