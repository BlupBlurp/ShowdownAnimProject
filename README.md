# ShowdownAnimProject

sudo python record_sprites.py --output-dir ./input/gen1-back --start-index 40 --count 350
python sprite_pipeline.py ./input/gen1-front/*.mp4 --output-dir ./output --greenscreen --fuzz 100 --loop-threshold 5

python rename_sprites.py --ani-dir ./References/ani-back --scale 1.5

I should also maybe manually copy the originals out or into a folder to be able to do multiple resize tests if needed. A lot of space tho