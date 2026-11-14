from hwfont_schema import check_sample_em

from font_gen.synth import synth_sample


def test_synth_samples_conform_for_every_position_and_kind():
    for label in ("a", "T", "i", "fi", "tion", ".", "7"):
        for pos in ("init", "medi", "fina", "isol"):
            sample, strokes, raster = synth_sample(label, pos, 0, seed=1)
            assert check_sample_em(sample, strokes, raster) == [], (label, pos)
