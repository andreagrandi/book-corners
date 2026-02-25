# Book Corners
A community-driven directory of little free libraries

## Seed local data

Use the management command below to reset and generate sample `Library` records:

```bash
python manage.py seed_libraries --reset --count 36 --images-dir libraries_examples --seed 42
```

- `--reset` deletes existing `Report` and `Library` rows first
- `--count` controls how many libraries are generated
- `--images-dir` points to local seed images (images are reused automatically)
- `--seed` makes generated data deterministic

If no images are found in the selected directory, the command automatically generates placeholder images.
