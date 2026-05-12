"""Pytest suite for multi_test.py functions."""
import sys
sys.path.insert(0, '/tmp')

from multi_test import add, subtract, multiply


def test_add_positive():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -1) == -2


def test_add_zero():
    assert add(0, 0) == 0


def test_subtract_positive():
    assert subtract(10, 4) == 6


def test_subtract_negative():
    assert subtract(3, 7) == -4


def test_subtract_zero():
    assert subtract(5, 0) == 5


def test_multiply_positive():
    assert multiply(3, 4) == 12


def test_multiply_negative():
    assert multiply(-2, 3) == -6


def test_multiply_zero():
    assert multiply(99, 0) == 0


def test_multiply_one():
    assert multiply(7, 1) == 7
