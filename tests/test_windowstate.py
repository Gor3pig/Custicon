import unittest

from custicon.service import WindowState
from custicon.ui import windowstate
from custicon.ui.windowstate import DEFAULT_HEIGHT, DEFAULT_WIDTH, MINIMUM_HEIGHT, MINIMUM_WIDTH, SCREEN_SHARE, fitted, initial_state

BIG = (3840, 2160)
SMALL = (1366, 768)


class FittedTests(unittest.TestCase):
    def test_a_size_that_fits_is_kept(self):
        self.assertEqual(fitted(900, 600, BIG), (900, 600))

    def test_a_size_that_is_too_wide_is_reduced_to_the_screen_share(self):
        self.assertEqual(fitted(5000, 600, SMALL)[0], int(SMALL[0] * SCREEN_SHARE))

    def test_a_size_that_is_too_tall_is_reduced_to_the_screen_share(self):
        self.assertEqual(fitted(900, 5000, SMALL)[1], int(SMALL[1] * SCREEN_SHARE))

    def test_the_two_sides_are_limited_apart(self):
        self.assertEqual(fitted(900, 5000, SMALL), (900, int(SMALL[1] * SCREEN_SHARE)))
        self.assertEqual(fitted(5000, 600, SMALL), (int(SMALL[0] * SCREEN_SHARE), 600))

    def test_without_a_known_screen_nothing_is_reduced(self):
        self.assertEqual(fitted(5000, 4000, None), (5000, 4000))

    def test_a_tiny_screen_never_goes_below_the_minimum(self):
        self.assertEqual(fitted(900, 600, (300, 300)), (MINIMUM_WIDTH, MINIMUM_HEIGHT))

    def test_a_tiny_size_is_raised_to_the_minimum(self):
        self.assertEqual(fitted(10, 10, BIG), (MINIMUM_WIDTH, MINIMUM_HEIGHT))


class InitialStateTests(unittest.TestCase):
    def test_a_first_launch_uses_the_default_size(self):
        self.assertEqual(initial_state(None, BIG), (DEFAULT_WIDTH, DEFAULT_HEIGHT, False))

    def test_a_first_launch_on_a_small_screen_fits_it(self):
        width, height, maximized = initial_state(None, (1024, 768))
        self.assertLessEqual(width, int(1024 * SCREEN_SHARE))
        self.assertLessEqual(height, int(768 * SCREEN_SHARE))
        self.assertFalse(maximized)

    def test_a_remembered_size_wins_over_the_default(self):
        self.assertEqual(initial_state(WindowState(1200, 800, False), BIG), (1200, 800, False))

    def test_a_remembered_maximized_state_is_returned(self):
        self.assertEqual(initial_state(WindowState(1200, 800, True), BIG), (1200, 800, True))

    def test_a_remembered_size_too_big_for_this_screen_is_reduced(self):
        width, height, _maximized = initial_state(WindowState(3000, 2000, False), SMALL)
        self.assertEqual((width, height), (int(SMALL[0] * SCREEN_SHARE), int(SMALL[1] * SCREEN_SHARE)))

    def test_the_default_is_wide_enough_for_two_columns(self):
        self.assertGreaterEqual(DEFAULT_WIDTH, 900)
        self.assertGreaterEqual(DEFAULT_HEIGHT, 640)


class ScreenSizeTests(unittest.TestCase):
    def test_without_a_display_there_is_no_size(self):
        from unittest import mock

        with mock.patch.object(windowstate.Gdk.Display, "get_default", return_value=None):
            self.assertIsNone(windowstate.screen_size())
