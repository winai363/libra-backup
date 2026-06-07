"""Regression tests for fail-closed KDP update confirmation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kdp_upload import is_kdp_publish_confirmed


def test_bookshelf_url_confirms_publish():
    assert is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/bookshelf",
        "",
    )


def test_explicit_submission_message_confirms_publish():
    assert is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/title-setup/kindle/BOOK/pricing",
        "Your changes have been submitted for review.",
    )


def test_content_save_success_does_not_confirm_publish():
    assert not is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/title-setup/kindle/BOOK/content",
        "Your manuscript was uploaded successfully.",
    )


def test_pricing_page_without_submission_does_not_confirm_publish():
    assert not is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/title-setup/kindle/BOOK/pricing",
        "Save and Publish your Kindle eBook",
    )
