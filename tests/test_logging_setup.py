"""Tests for progress_tracker.logging_setup."""

from __future__ import annotations

import logging

from progress_tracker.logging_setup import configure_logging


def test_configure_logging_sets_given_level(reset_root_logger) -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_is_case_insensitive(reset_root_logger) -> None:
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_falls_back_to_info_for_unknown(reset_root_logger) -> None:
    configure_logging("NOT_A_REAL_LEVEL")
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_default_level_is_info(reset_root_logger) -> None:
    configure_logging()
    assert logging.getLogger().level == logging.INFO
