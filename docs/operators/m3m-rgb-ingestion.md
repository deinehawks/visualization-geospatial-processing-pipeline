# DJI M3M RGB ingestion

Use the UAV-folder selector and RGB image selector independently:

```powershell
python main.py `
  --survey "BCO-121_11Ha_M3M_70m_85f75s_5mps" `
  --year 2026 `
  --uav M3M_A `
  --rgb
```

`--uav` restricts dataset discovery to an exact, case-insensitive parent-folder
name. It is not limited to `M3M_A` through `M3M_D`. Duplicate dataset names
remaining after this filter keep the existing date hint and interactive
full-path selection behavior.

`--rgb` accepts only filenames ending case-insensitively in `_D.JPG`.
Multispectral `.TIF` bands and nonmatching JPEG files are not copied. Every
nested directory below the selected dataset is scanned, so `1of2`, `1of10`,
`15of15`, larger splits, and unexpected split-folder names are combined
without parsing the folder name.

Accepted images are flattened into the existing `images/raw` layout.
Case-insensitive duplicate destination names fail before copying rather than
overwriting. KML/KMZ discovery remains recursive within the selected dataset.

Both options are invocation-scoped. An ordinary resume uses the persisted
source directory and already segregated raw images. Repeat `--rgb` if data
segregation itself must run again. Supplying `--uav` during resume validates
the persisted source and fails if the folder does not match.

Multispectral processing and a future `--ms` flag are not implemented.
