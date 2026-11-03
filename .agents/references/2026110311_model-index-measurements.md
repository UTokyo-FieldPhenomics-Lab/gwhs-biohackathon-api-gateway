# The model index's measured column

**2026-11-03.** The models index carried a `measured` column beside each id. It moved here: the
public table is a catalog of what each model is and which task it answers, and a reader scanning for
the segmentation model should not have to read a benchmark. The values below are the column's text,
unchanged. Each model page keeps its own measured detail; this is the index's copy, not a second
measurement.

| index row | measured |
|---|---|
| `sam3.1-meta` | 0.36 s per inference on a 3090 at 1008; parameters and their encodings on the model page |
| `gwfss-segformer-b4` | 0.038 s per inference on a 3090; mIoU 0.70 on the ten published rows, stems the weak class; numbers on the model page |
| `tasselnetv4` | 0.31 s per inference through the API; MAE 8.75 on `salaamuko` (48 images) and 10.8 over 32 sampled species; numbers on the model page |
| `internvl-u` | 0.67 s for a sentence and 11 s for a picture on a 4090; 1.0 MB PNG answer; numbers on the model page |
| `dinov3-vitl16` | 0.039 s per inference on a 3090 at 512; 2.8 MB answer at the default, 6.3 MB at the ceiling; numbers on the model page |
| `dinov3-vith16plus` | 0.026 s on a 4:6 frame at 384 on a 3090; 2.0 MB answer at the default, 5.5 MB at the ceiling; numbers on the model page |

## The index after the change

- **General:** `sam3.1-meta`, `internvl-u`, and DINOv3 (one row, two ids: `dinov3-vitl16` and
  `dinov3-vith16plus`). These are upstream checkpoints, not trained on this project's data.
- **Specialized:** `gwfss-segformer-b4` and `tasselnetv4`, trained for a wheat or plant task.
- **Tags** replace the measured column: `segmentation`, `detection`, `vision-language`,
  `text-to-image`, `image-editing`, `features`, `counting`.

The DINOv3 row is one position in the catalog, not two: the two ids share the page, the contract and
the task, and only the checkpoint differs. The ids remain separate in the API, because each is a
different set of weights and a different amount of admitted memory.
