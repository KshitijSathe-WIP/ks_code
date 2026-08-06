"""Tests for authorisation logic."""
import pytest
from unittest.mock import patch, MagicMock
from functions.shared.models import AuthorisationError


class TestAuthorisation:

    def _assert_authorised(self, actor, pm, tm, em, pmo_member=False):
        with patch(
            "functions.apply_update.authz._is_pmo_member",
            return_value=pmo_member
        ):
            from functions.apply_update.authz import assert_authorised
            assert_authorised(actor, pm, tm, em)

    def test_pm_is_authorised(self):
        self._assert_authorised("pm@wipro.com", "pm@wipro.com", "tm@wipro.com", "em@wipro.com")

    def test_tm_is_authorised(self):
        self._assert_authorised("tm@wipro.com", "pm@wipro.com", "tm@wipro.com", "em@wipro.com")

    def test_em_is_authorised(self):
        self._assert_authorised("em@wipro.com", "pm@wipro.com", "tm@wipro.com", "em@wipro.com")

    def test_pmo_member_is_authorised(self):
        self._assert_authorised("pmo@wipro.com", "pm@wipro.com", "tm@wipro.com", "em@wipro.com", pmo_member=True)

    def test_non_owner_non_pmo_rejected(self):
        with patch("functions.apply_update.authz._is_pmo_member", return_value=False):
            from functions.apply_update.authz import assert_authorised
            with pytest.raises(AuthorisationError):
                assert_authorised("random@wipro.com", "pm@wipro.com", "tm@wipro.com", "em@wipro.com")

    def test_case_insensitive_email_match(self):
        self._assert_authorised("PM@Wipro.Com", "pm@wipro.com", "tm@wipro.com", "em@wipro.com")
