#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f'{label} anchor not found')
    return text.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()

    project = Path(args.project_dir).resolve()
    target = project / 'src/http/mediaRoutes.ts'
    text = target.read_text(encoding='utf-8')

    old_serve_file = """async function serveFile(res: Response, file: string): Promise<void> {\n  const stat = await fs.stat(file);\n  if (!stat.isFile()) throw Object.assign(new Error('Media file not found'), { statusCode: 404 });\n"""
    new_serve_file = """async function serveFile(res: Response, file: string): Promise<void> {\n  let stat;\n  try {\n    stat = await fs.stat(file);\n  } catch (error) {\n    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {\n      throw Object.assign(new Error('Media file not found'), { statusCode: 404 });\n    }\n    throw error;\n  }\n  if (!stat.isFile()) throw Object.assign(new Error('Media file not found'), { statusCode: 404 });\n"""
    text = replace_once(text, old_serve_file, new_serve_file, 'serveFile ENOENT mapping')

    old_route = """      authorizeMedia(req, channelId, 'live');\n      await serveFile(res, safeFile(liveRoot(channelId, found.channel.archive_storage), relative));\n"""
    new_route = """      authorizeMedia(req, channelId, 'live');\n      const root = liveRoot(channelId, found.channel.archive_storage);\n      // FFmpeg writes live-only segments into root/segments, but its HLS\n      // playlist contains basename-only URI entries such as seg_000000001.ts.\n      // Resolve those basename entries against the physical segments directory.\n      const mediaRoot = found.channel.archive_storage === 'device' && !relative.includes('/')\n        ? path.join(root, 'segments')\n        : root;\n      await serveFile(res, safeFile(mediaRoot, relative));\n"""
    text = replace_once(text, old_route, new_route, 'live segment storage route')

    target.write_text(text, encoding='utf-8')
    print('Hikvision live segment storage path prepared')


if __name__ == '__main__':
    main()
