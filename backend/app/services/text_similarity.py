"""How alike two descriptions are, in one place.

Token overlap over the longer side. It lived in `connection_service`,
where the bank-sync fuzzy merge tuned it, and was reproduced inside the
matching engine because that module is pure: it holds no session and
imports no service, so it could not reach across to a module that pulls in
models and SQLAlchemy.

Two copies of one formula drift, and this one is a threshold people tune
against. So it moves here instead: a function with no dependencies, which
the pure engine may import without giving up what makes it pure.

It is deliberately unforgiving. A bank string and a hand-typed one rarely
share tokens exactly, which is why the exact-amount signal carries the
weight and this only guards against two promises of the same value.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


def token_overlap(a: Optional[str], b: Optional[str]) -> float:
    """0 when nothing is shared, 1 when the words are the same set."""
    if not a or not b:
        return 0.0
    tokens_a = {token for token in a.lower().split() if token}
    tokens_b = {token for token in b.lower().split() if token}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


#: Shorter than this, a name run together is not distinctive enough to be
#: looked for inside a statement line: "ai" or "tv" occur everywhere.
_COMPACT_MIN = 5

#: What a containment hit is worth. Every word of the shorter side found
#: in the longer one is strong evidence, but weaker than the two reading
#: the same, so it sits below 1.0 and an identical pair still ranks first
#: when a caller sorts several candidates by score.
_CONTAINED_WEIGHT = 0.9


def description_match(a: Optional[str], b: Optional[str]) -> float:
    """How alike a hand-typed name and a statement line are, either way round.

    `token_overlap` divides by the longer side, which is right for two
    statement lines and wrong for the pair recurring matching actually
    sees: a person types *Netflix* and the bank prints
    `NETFLIX.COM 866-579-7172 CA`. One word shared out of four is 0.25,
    `PAYPAL *SPOTIFY*P47A2033189708 NY` against *Spotify* is 0.0 because
    the asterisks glue the name to its neighbours, and so every real
    charge failed the 0.6 bar and sat next to its own placeholder as a
    duplicate.

    So three readings are taken and the best one kept:

      - the plain overlap, unchanged, so two statement lines still grade
        as they always did;
      - **containment**: the share of the shorter side's words found in
        the longer side, as a whole word or (for words of four letters
        or more) as the start of one, the same rule `names_account`
        trusts; a two-word name needs both words, so *Google One* does
        not take a `GOOGLE *YOUTUBE TV` charge of the same value;
      - the name run together as a substring of the line run together,
        for banks that print `YOUTUBETV` as one word.

    Words are normalised first (case, accents, punctuation), so *Disney+*
    reads as *disney* and `MIKO.AI` as two words. Order does not matter:
    the placeholder is the movement in one lookup and the candidate in
    the other.
    """
    overlap = token_overlap(a, b)
    words_a, words_b = normalize_words(a), normalize_words(b)
    if not words_a or not words_b:
        return overlap
    shorter, longer = (
        (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    )
    wanted = set(shorter)
    printed = set(longer)
    hits = sum(
        1
        for word in wanted
        if word in printed
        or (
            len(word) >= _PREFIX_MIN
            and any(token.startswith(word) for token in printed)
        )
    )
    contained = hits / len(wanted)
    compact = "".join(shorter)
    if len(compact) >= _COMPACT_MIN and compact in "".join(longer):
        contained = 1.0
    return max(overlap, contained * _CONTAINED_WEIGHT)


#: Words that name a kind of account rather than a particular one.
#: Without this list "Conta Corrente" would match every statement line
#: carrying the word *conta*, which in Brazil is most of them, and the
#: signal would be worse than not having it.
#:
#: Portuguese and English only, deliberately. These are matched against
#: what a **bank** printed, and the two languages cover the statements
#: this runs on; a Polish stopword that is nobody's account name costs a
#: reader of this list more than it buys.
_GENERIC_ACCOUNT_WORDS = frozenset(
    {
        "account",
        "accounts",
        "balance",
        "bank",
        "banco",
        "banking",
        "card",
        "cartao",
        "cash",
        "checking",
        "conta",
        "corrente",
        "credit",
        "credito",
        "current",
        "debit",
        "debito",
        "deposit",
        "deposito",
        "dinheiro",
        "investimento",
        "investment",
        "joint",
        "main",
        "money",
        "poupanca",
        "principal",
        "salario",
        "salary",
        "savings",
        "transfer",
        "transferencia",
        "wallet",
        "carteira",
    }
)

#: Below this a token is not identifying enough to be trusted on its own
#: as a substring, so it has to land as a whole word.
_PREFIX_MIN = 4


def normalize_words(value: Optional[str]) -> list[str]:
    """Lowercase, unaccented, punctuation-free words.

    Accents come off because the same bank prints the same account as
    *Itaú* in one field and *ITAU* in another, and a signal that reads one
    and not the other is a signal that works for half the statements in
    the country it was written for.
    """
    if not value:
        return []
    folded = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [word for word in re.split(r"[^0-9a-z]+", stripped) if word]


def names_account(text: Optional[str], account_name: Optional[str]) -> bool:
    """Does this text name that account?

    The one signal that reliably tells two same-amount transfers apart.
    When money leaves an account on the same day for two destinations,
    the amounts are identical, the dates are identical, and the *only*
    thing that says which is which is that one line reads
    `To FORTUNEO ACCOUNT` and the other reads `To Trade Republic`.

    Generic words are dropped first, so an account called *Conta
    Corrente* names nothing and this returns False rather than matching
    every line on the statement. What is left has to land either as a
    whole word or as the start of one, which is what catches the
    `NUBANK*IFOOD` and `fortuneo-account` shapes banks actually print
    without letting three letters match anywhere they happen to occur.
    """
    wanted = [
        word
        for word in normalize_words(account_name)
        if word not in _GENERIC_ACCOUNT_WORDS and len(word) >= 3
    ]
    if not wanted:
        return False

    printed = normalize_words(text)
    if not printed:
        return False

    exact = set(printed)
    for word in wanted:
        if word in exact:
            return True
        if len(word) >= _PREFIX_MIN and any(
            token.startswith(word) for token in printed
        ):
            return True
    return False
