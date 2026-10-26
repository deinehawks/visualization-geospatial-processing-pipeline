"""Task-4-only fresh QGIS rendering; ordinary resume remains in RGBPipeline."""
from __future__ import annotations

import json
from pathlib import Path

from modules.qgis.qgis_tools import QGISTools
from shared.artifacts import create_run_workspace
from shared.execution import resource_scope
from shared.publication_lock import acquire_publication_lock, release_publication_lock
from shared.qgis_refresh import (
    QGISRefreshError, file_digest, inventory, new_generation, publish_refresh,
    safe_path, write_record,
)
from shared.raster_metadata import validate_raster_manifest
from shared.stage_runner import StageFailedWithOutput, StageRequiresRecovery


class RGBQGISRefreshMixin:
    def _stage_qgis_refresh(self):
        logger = self.loggers['qgis']
        rgb = self._require_rgb_path()
        survey_id = self._require_survey_id()
        workspace = self.workspace_layout.root
        cfg = self.config.get('qgis') or {}
        if not cfg.get('enabled', True) or not (cfg.get('clip') or {}).get('enabled', True) or not (cfg.get('tiles') or {}).get('enabled', True):
            raise StageFailedWithOutput('Fresh QGIS refresh requires enabled QGIS, clipping and tiles')
        web = self.state.get('webodm') or {}
        task = web.get('task4') or {}
        binding = self.repo.get_webodm_binding(self.run_id, 'task4')
        if (not binding or not binding.get('task_id') or binding.get('success') != 1 or not task.get('success')
                or str(binding['task_id']) != str(task.get('id'))
                or str(binding['project_id']) != str(web.get('project_id'))):
            raise StageRequiresRecovery('Fresh QGIS refresh requires a matching completed Task 4 binding')
        selected = self._select_existing_task_orthomosaic(
            task_key='task4', flag=self._webodm_task_flag('task4', default_boundary_mode='b'),
            boundary_used=True, require_exists=True,
        )
        source = safe_path(rgb / 'ortho', Path(selected['source_path']))
        if not source.is_file() or not source.stat().st_size:
            raise StageFailedWithOutput('Task 4 orthomosaic is missing or empty')
        boundary = safe_path(rgb / 'boundary', Path(self.state.get('boundary_geojson_path') or ''))
        if not boundary.is_file() or not boundary.stat().st_size:
            raise StageFailedWithOutput('Task 4 boundary is missing or empty')
        previous = self.repo.get_latest_stage_output(self.run_id, 'qgis') or {}
        operations = previous.get('operations') or {}
        scoped = 'task4' in operations
        if not scoped and previous.get('selected_webodm_task') != 'task4':
            raise StageFailedWithOutput('Cannot establish Task 4 ownership of previous unscoped QGIS outputs')
        if self.published_layout.publication_manifest.exists():
            raise StageFailedWithOutput('An authoritative publication set exists; use its complete-set workflow, not legacy QGIS refresh')
        filename = self._clipped_orthomosaic_filename(task_key='task4', flag=selected['flag'])
        published_clip = rgb / 'qgis' / 'clipped' / 'ortho' / filename
        published_tiles = rgb / 'tiles' / 'ortho' / 'round-corners'
        workspace_clip = self.workspace_layout.qgis_clipped_ortho / filename
        workspace_tiles = self.workspace_layout.qgis_tiles_round
        if scoped:
            published_tiles, workspace_tiles = published_tiles / 'task4', workspace_tiles / 'task4'
        elif (published_tiles / 'task2').exists() or (workspace_tiles / 'task2').exists():
            raise StageFailedWithOutput('Unscoped refresh would overlap Task 2; refusing')
        create_run_workspace(self.workspace_layout)
        generation = new_generation(workspace, self.run_id)
        journal = generation / 'refresh.json'
        settings = cfg.get('tools') or {}
        tools = QGISTools(logger=logger,
            qgis_root=str(settings.get('qgis_root') or ''),
            gdalwarp_path=str(settings.get('gdalwarp_path') or 'gdalwarp'),
            gdal2tiles_path=str(settings.get('gdal2tiles_path') or 'gdal2tiles.py'),
            gdalinfo_path=str(settings.get('gdalinfo_path') or 'gdalinfo'),
            **({'execution': self.execution} if self.execution is not None else {}))
        clipped = generation / filename
        tiles = generation / 'tiles'
        tile_cfg = cfg.get('tiles') or {}
        zoom = str(tile_cfg.get('zoom') or '11-24')
        profile = str(tile_cfg.get('profile') or 'mercator')
        try:
            self._check_control_or_raise('qgis')
            self._check_write_capacity(role='qgis_local_staging', path=generation,
                                       required_bytes=max(20 * 1024 ** 3, source.stat().st_size * 6))
            source_hash, boundary_hash = file_digest(source), file_digest(boundary)
            logger.info('event=qgis_refresh_started task=task4 generation=%s source_sha256=%s', generation, source_hash)
            # Both destinations are new: no size-based staging cache, clip or tile reuse.
            nodata = (cfg.get('clip') or {}).get('dst_nodata')
            nodata = float(nodata) if nodata is not None and str(nodata).strip() else None
            tools.clip_raster_by_mask(input_tif=source, mask_geojson=boundary,
                output_tif=clipped, dst_nodata=nodata)
            tools.verify_raster_readable(clipped, retries=1, delay_s=0)
            self._check_control_or_raise('qgis')
            tools.generate_tiles(input_tif=clipped, output_dir=tiles, zoom=zoom,
                profile=profile, webviewer=str(tile_cfg.get('webviewer') or 'none'),
                copyright_text=str(tile_cfg.get('copyright') or 'ASIMOV-HAWKS'),
                clean=False, resume=False)
            tile_record = inventory(tiles, png=True)
            with resource_scope(self, 'compute'):
                source_key, source_record = self._prepare_raster_manifest_entry(
                    tif_path=source, published_path=source, role='webodm_orthomosaic', task_key='task4')
                clipped_key, clipped_record = self._prepare_raster_manifest_entry(
                    tif_path=clipped, published_path=published_clip, role='qgis_clipped', task_key='task4')
            if file_digest(source) != source_hash or file_digest(boundary) != boundary_hash:
                raise QGISRefreshError('Source TIFF or boundary changed while rendering; nothing published')
            manifest = self.published_layout.manifest
            original_manifest = validate_raster_manifest(manifest, survey_id=survey_id)
            payload = dict(original_manifest)
            payload['raster_outputs'] = dict(payload.get('raster_outputs') or {})
            payload['raster_outputs'].update({source_key: source_record, clipped_key: clipped_record})
            payload['qgis_refresh_task4'] = {'run_id': self.run_id, 'generation': generation.name,
                'source_sha256': source_hash, 'boundary_sha256': boundary_hash, 'tiles': tile_record}
            new_manifest = generation / 'manifest.json'
            write_record(new_manifest, payload)
            targets = [(workspace, clipped, workspace_clip), (workspace, tiles, workspace_tiles),
                       (rgb, clipped, published_clip), (rgb, tiles, published_tiles)]
            staging_cfg = cfg.get('local_staging') or {}
            staging_value = str(staging_cfg.get('dir') or '').strip()
            if staging_cfg.get('enabled', True):
                if not staging_value:
                    cache = (self.config.get('paths') or {}).get('upload_cache_root')
                    if not cache:
                        raise QGISRefreshError('Fresh refresh requires an explicit local staging or upload cache root')
                    staging_value = str(Path(cache) / 'qgis-staging')
                staging = Path(staging_value)
                targets.append((staging, None, staging / 'tiles' / self.run_id / 'task4'))
            # Metadata is installed last, after all fresh data targets.
            targets.append((rgb, new_manifest, manifest))
            bytes_new = clipped.stat().st_size + tile_record['total_bytes'] + new_manifest.stat().st_size
            self._check_write_capacity(role='qgis_tile_copy_back', path=workspace, required_bytes=bytes_new)
            self._check_write_capacity(role='published_directory_mirror', path=rgb, required_bytes=bytes_new)
            self._check_control_or_raise('qgis')
            with resource_scope(self, 'bulk_transfer'):
                lock = acquire_publication_lock(published_root=rgb, run_id=self.run_id, survey_id=survey_id)
                try:
                    if validate_raster_manifest(manifest, survey_id=survey_id) != original_manifest:
                        raise QGISRefreshError('Survey manifest changed')
                    def verify_inputs():
                        if file_digest(source) != source_hash or file_digest(boundary) != boundary_hash:
                            raise QGISRefreshError('Source TIFF or boundary changed before publication')
                    refresh = publish_refresh(generation, targets,
                        check_control=lambda: self._check_control_or_raise('qgis'),
                        before_activation=verify_inputs)
                finally:
                    release_publication_lock(lock)
        except BaseException as exc:
            record = json.loads(journal.read_text(encoding='utf-8'))
            if record.get('state') in ('publishing', 'published', 'requires_recovery'):
                raise StageRequiresRecovery('Fresh QGIS publication needs reconciliation',
                    output={'qgis_refresh': {'manifest_path': str(journal)}}) from exc
            if record.get('state') in ('building', 'preparing'):
                record['state'] = 'failed'
                write_record(journal, record)
            raise
        selected.update(clipped_path=str(published_clip), clipped_filename=filename, tiles_dir=str(published_tiles))
        result = {'selected_webodm_task': 'task4', 'selected_orthomosaic': selected,
            'boundary_geojson': str(boundary), 'qgis_refresh': refresh,
            'clip': {'enabled': True, 'input': str(source), 'output': str(published_clip)},
            'tiles': {'enabled': True, 'mode': 'round-corners', 'output_dir': str(published_tiles), 'zoom': zoom, 'profile': profile},
            'workspace': {'qgis_clipped_ortho': str(workspace_clip), 'tiles_dir': str(workspace_tiles)},
            'published': {'qgis_clipped_ortho': str(published_clip), 'tiles_dir': str(published_tiles)},
            'published_operation': {'qgis_clipped_ortho': str(published_clip), 'tiles_dir': str(published_tiles)}}
        if scoped:
            # Preserve Task 2's exact recorded output and avoid regenerating it.
            result['operations'] = {**operations, 'task4': dict(result)}
            result['workspace'] = dict(result['workspace'])
            result['published'] = dict(result['published'])
            result['workspace']['operations'] = {
                key: value.get('workspace') for key, value in result['operations'].items()}
            result['published']['operations'] = {
                key: value.get('published_operation') for key, value in result['operations'].items()}
        self.state['selected_webodm_task'] = 'task4'
        self.state['selected_orthomosaic'] = selected
        logger.info('event=qgis_refresh_published task=task4 manifest=%s backups=%s',
                    refresh['manifest_path'], len(refresh['backups']))
        return result
