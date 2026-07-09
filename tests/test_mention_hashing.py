from __future__ import annotations

from ytdt.models import Comment, hash_text_mentions
from ytdt.utils import sha1_hex


def test_mentions_hashed_and_rest_kept():
    out = hash_text_mentions("thanks @alice for the tip")
    assert out == f"thanks @{sha1_hex('@alice')} for the tip"


def test_hash_links_to_pseudonymized_author_name():
    alice = Comment(comment_id="t1", author_name="@alice", text="original")
    bob = Comment(comment_id="r1", author_name="@bob", text="@alice I agree")
    p_alice, p_bob = alice.pseudonymized(), bob.pseudonymized()
    # the in-text mention hash equals alice's hashed authorName
    assert p_bob.text == f"@{p_alice.author_name} I agree"


def test_trailing_sentence_period_not_part_of_handle():
    out = hash_text_mentions("well said @alice.")
    assert out == f"well said @{sha1_hex('@alice')}."


def test_handle_with_inner_dots_and_digits():
    out = hash_text_mentions("cc @abc.def-99_x ok")
    assert out == f"cc @{sha1_hex('@abc.def-99_x')} ok"


def test_email_addresses_untouched():
    text = "contact me at bob@gmail.com please"
    assert hash_text_mentions(text) == text


def test_at_sign_without_handle_untouched():
    assert hash_text_mentions("great moment @ 3:20") == "great moment @ 3:20"
    assert hash_text_mentions("hi @a!") == "hi @a!"  # too short to be a handle


def test_multiple_mentions():
    out = hash_text_mentions("@alice and @bob both make good points")
    assert out == (
        f"@{sha1_hex('@alice')} and @{sha1_hex('@bob')} both make good points"
    )


def test_non_pseudonymized_text_untouched():
    comment = Comment(comment_id="c1", author_name="@bob", text="@alice hello")
    assert comment.text == "@alice hello"  # only pseudonymized() hashes
