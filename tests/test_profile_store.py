"""Сохранение профиля при смене имени приложения и удаление в режиме амнезии."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from branding import APP_NAME
from models import Profile
from ui_dialogs import ProfileStore


class ProfileStoreTest(unittest.TestCase):
    def test_previous_profile_remains_accessible_and_amnesia_removes_both_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = Profile("Сохранённый ник")
            previous = ProfileStore(path=root / "Эфир" / "profile.json")
            previous.save(saved, False)

            with patch("ui_dialogs.QStandardPaths.writableLocation", return_value=str(root / APP_NAME)):
                store = ProfileStore()
                self.assertEqual(store.load(Profile("Новый ник")), (saved, False))
                updated = Profile("Обновлённый ник")
                store.save(updated, False)
                self.assertEqual(store.load(saved), (updated, False))
                store.save(updated, True)
                self.assertFalse(store.path.exists())
                self.assertFalse(previous.path.exists())
                self.assertEqual(store.load(saved), (saved, True))
