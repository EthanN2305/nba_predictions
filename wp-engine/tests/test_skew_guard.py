"""Checkpoint 6.2 — training/serving skew guards.

The single most dangerous failure mode: feature code, the persisted
contract (feature_meta.json), and the model drifting apart silently.
"""

import json
import pickle
import shutil
from pathlib import Path

import pytest

from wp_engine.features import FEATURE_COLUMNS, FeatureBuilder
from wp_engine.train import load_predictor

from tests.test_features import make_state

PINNED_MODELS = Path(__file__).parent / "fixtures" / "models"


class TestContractAlignment:
    def test_feature_builder_output_matches_feature_columns(self):
        features = FeatureBuilder().update(make_state())
        assert list(features.keys()) == list(FEATURE_COLUMNS)

    def test_feature_meta_matches_feature_columns(self):
        meta = json.loads((PINNED_MODELS / "feature_meta.json").read_text())
        assert meta["feature_columns"] == list(FEATURE_COLUMNS)

    def test_model_expects_exactly_feature_columns(self):
        booster = pickle.loads((PINNED_MODELS / "model.pkl").read_bytes())
        assert booster.feature_name() == list(FEATURE_COLUMNS)


class TestStartupAssertion:
    def test_load_predictor_rejects_mismatched_meta(self, tmp_path):
        corrupt = tmp_path / "models"
        shutil.copytree(PINNED_MODELS, corrupt)
        meta = json.loads((corrupt / "feature_meta.json").read_text())
        meta["feature_columns"] = meta["feature_columns"][:-1] + ["haunted_column"]
        (corrupt / "feature_meta.json").write_text(json.dumps(meta))
        with pytest.raises(RuntimeError, match="skew"):
            load_predictor(models_dir=corrupt)

    def test_load_predictor_accepts_aligned_meta(self):
        predict = load_predictor(models_dir=PINNED_MODELS)
        assert callable(predict)
