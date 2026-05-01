# ShowdownAnimProject

python record_sprites.py --start-index 40 --count 350
python sprite_pipeline.py ./input/gen1-front/*.mp4 --output-dir ./output --greenscreen --fuzz 100 --loop-threshold 5
python rename_sprites.py
