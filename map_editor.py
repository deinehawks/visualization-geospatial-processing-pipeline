"""Desktop editor entry point. Importing it does not initialize QGIS or read .env."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description='Live orthomosaic print-layout editor')
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--package', type=Path, help='Existing exported map folder')
    inputs.add_argument('--design', type=Path, help='Saved .mapedit.json design')
    inputs.add_argument('--export-snapshot', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.export_snapshot:
        from modules.map_export.editor_session import run_snapshot_export
        run_snapshot_export(args.export_snapshot)
        return 0

    from qgis.core import QgsApplication
    import os
    prefix = os.environ.get('QGIS_PREFIX_PATH')
    if not prefix:
        raise RuntimeError('Use run_map_editor.bat to launch with the QGIS environment')
    QgsApplication.setPrefixPath(prefix, True)
    app = QgsApplication([], True)
    app.initQgis()
    from modules.map_export.editor_window import create_window_class
    window = create_window_class()()
    window.show()
    try:
        if args.design:
            from modules.map_export.editor_model import load_design
            window.install_design(load_design(args.design), args.design)
        elif args.package:
            window.load_package(args.package)
    except Exception as exc:
        window.error(exc)
    result = app.exec_()
    del window
    app.exitQgis()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
