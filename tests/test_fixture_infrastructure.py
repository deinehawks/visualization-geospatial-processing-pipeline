from pathlib import Path


def test_temporary_path_layout_is_owned_by_tmp_path(tmp_path, temporary_path_layout):
    for value in vars(temporary_path_layout).values():
        assert isinstance(value, Path)
        assert value == tmp_path or tmp_path in value.parents

    for directory in (
        temporary_path_layout.application_root,
        temporary_path_layout.data_dir,
        temporary_path_layout.logs_dir,
        temporary_path_layout.surveys_dir,
        temporary_path_layout.field_data_dir,
        temporary_path_layout.upload_cache_dir,
        temporary_path_layout.checkpoint_dir,
    ):
        assert directory.is_dir()

    assert temporary_path_layout.database_path.is_file()


def test_sample_directories_and_images_are_small_and_composable(
    tmp_path,
    sample_survey_dir,
    sample_dataset_dir,
    placeholder_image_files,
):
    assert tmp_path in sample_survey_dir.parents
    assert tmp_path in sample_dataset_dir.parents
    assert all(path.parent == sample_dataset_dir for path in placeholder_image_files)
    assert all(path.stat().st_size < 100 for path in placeholder_image_files)


def test_temporary_application_directories_are_isolated(tmp_path_factory):
    first = tmp_path_factory.mktemp("application-one")
    second = tmp_path_factory.mktemp("application-two")
    (first / "sentinel.txt").write_text("first", encoding="utf-8")

    assert first != second
    assert not (second / "sentinel.txt").exists()
