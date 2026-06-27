"""Tests for eurlex.model.short_label -- the distinctive human handle derived
from an EU act's official title (shown in the browse index instead of the bare
CELEX, and stored on the artifact)."""

from accommodanda.eurlex.model import short_label


def test_no_official_label_trims_to_designation_and_subject():
    # the two reference cases: drop issuing body + act type + date, keep the
    # "(EU) YYYY/NN" designation and the substantive subject
    assert short_label(
        "Rådets direktiv (EU) 2022/2523 av den 14 december 2022 om "
        "säkerställande av en global minimiskattenivå för multinationella "
        "koncerner och storskaliga nationella koncerner i unionen"
    ) == ("(EU) 2022/2523 Säkerställande av en global minimiskattenivå för "
          "multinationella koncerner och storskaliga nationella koncerner i unionen")


def test_drops_date_cross_reference_tail_and_eea_boilerplate():
    assert short_label(
        "Kommissionens genomförandeförordning (EU) 2020/16 av den 10 januari "
        "2020 om godkännande för utsläppande på marknaden av "
        "nikotinamidribosidklorid som ett nytt livsmedel enligt "
        "Europaparlamentets och rådets förordning (EU) 2015/2283 och om ändring "
        "av kommissionens genomförandeförordning (EU) 2017/2470 "
        "(Text av betydelse för EES)"
    ) == ("(EU) 2020/16 Godkännande för utsläppande på marknaden av "
          "nikotinamidribosidklorid som ett nytt livsmedel")


def test_prefers_official_short_title_in_trailing_parenthesis():
    assert short_label(
        "Europaparlamentets och rådets förordning (EU) 2016/679 av den 27 april "
        "2016 om skydd för fysiska personer ... och om upphävande av direktiv "
        "95/46/EG (allmän dataskyddsförordning) (Text av betydelse för EES)"
    ) == "(EU) 2016/679 Allmän dataskyddsförordning"


def test_directive_suffixed_numbering_is_a_designation():
    # old-style directive numbering ("2003/49/EG", no parentheses)
    assert short_label(
        "Rådets direktiv 2003/49/EG av den 3 juni 2003 om ett gemensamt system "
        "för beskattning av räntor och royalties som betalas mellan närstående "
        "bolag i olika medlemsstater"
    ) == ("2003/49/EG Ett gemensamt system för beskattning av räntor och "
          "royalties som betalas mellan närstående bolag i olika medlemsstater")


def test_trailing_abbreviation_marker_is_not_taken_as_a_name():
    # "(SUB)" is a quality-scheme marker, not a naming short title -- so the act
    # falls back to designation + subject, not the bare "SUB"
    out = short_label(
        "Kommissionens förordning (EG) nr 885/2005 av den 10 juni 2005 om "
        "komplettering av bilagan till förordning (EG) nr 2400/96 när det gäller "
        "upptagandet av en beteckning (Tørrfisk fra Lofoten) (SUB)")
    assert out.startswith("(EG) nr 885/2005 Komplettering av bilagan")
    assert out != "SUB"


def test_empty_title_is_none():
    assert short_label("") is None
    assert short_label(None) is None
