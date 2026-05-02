# ShowdownAnimProject

python record_sprites.py --output-dir ./input/gen1-back --start-index 40 --count 350
python sprite_pipeline.py ./input/gen1-front/*.mp4 --output-dir ./output --greenscreen --fuzz 100 --loop-threshold 5
python rename_sprites.py --scale 1.5 (1.5 is the base scale used in showdown for back sprites, front is fine with default for now)
