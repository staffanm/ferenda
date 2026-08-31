"""Tests for eurlex.model.short_label -- the distinctive human handle derived
from an EU act's official title (shown in the browse index instead of the bare
CELEX, and stored on the artifact)."""

from ferenda.eurlex.model import official_short_title, short_label

# the cyberresilience regulation (CRA): its short title is a single compound
# word in the trailing parenthesis, ahead of the EEA-relevance boilerplate
CRA_TITLE = (
    "Europaparlamentets och rådets förordning (EU) 2024/2847 av den 23 oktober "
    "2024 om övergripande cybersäkerhetskrav för produkter med digitala element "
    "och om ändring av förordningarna (EU) nr 168/2013 och (EU) 2019/1020 och "
    "direktiv (EU) 2020/1828 (cyberresiliensförordningen) (Text av betydelse "
    "för EES)")


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


def test_reviewer_example_and_konsolidering_boilerplate():
    # the USAGE_REVIEW E3 example: body/date/repeal-tail/EEA all trimmed
    assert short_label(
        "Europaparlamentets och rådets förordning (EU) 2016/426 av den 9 mars "
        "2016 om anordningar för förbränning av gasformiga bränslen och om "
        "upphävande av direktiv 2009/142/EG (Text av betydelse för EES)"
    ) == "(EU) 2016/426 Anordningar för förbränning av gasformiga bränslen"
    # a "(Konsolidering)" marker is boilerplate too (E3)
    assert short_label(
        "Rådets förordning (EG) nr 1/2003 om tillämpning (Konsolidering)"
    ) == "(EG) nr 1/2003 Tillämpning"


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


def test_single_word_official_short_title_is_taken():
    # regression: the short title sits in the trailing parenthesis even when it is
    # a single Swedish compound word (no space) -- the extractor must not require a
    # multi-word name, or it falls back to the subject ("Övergripande cyber...")
    assert short_label(CRA_TITLE) == "(EU) 2024/2847 Cyberresiliensförordningen"


def test_official_short_title_extracts_bare_capitalised_name():
    assert official_short_title(CRA_TITLE) == "Cyberresiliensförordningen"
    assert official_short_title(
        "... om upphävande av direktiv 95/46/EG (allmän dataskyddsförordning) "
        "(Text av betydelse för EES)") == "Allmän dataskyddsförordning"


def test_official_short_title_none_without_naming_parenthesis():
    # a subject-only title, an all-caps quality marker, and an empty title all
    # carry no naming short title
    assert official_short_title(
        "Rådets direktiv (EU) 2022/2523 av den 14 december 2022 om "
        "säkerställande av en global minimiskattenivå") is None
    assert official_short_title("Något (EU) 2020/1 om något (SUB)") is None
    assert official_short_title("") is None
    assert official_short_title(None) is None


def test_empty_title_is_none():
    assert short_label("") is None
    assert short_label(None) is None


# the NIS2 corrigendum: its title names the act it corrects and ends with that
# act's OJ coordinate, which read as a short title labelled 1 644 corrigenda
# "(EU) 2022/2555 Europeiska unionens officiella tidning L 333 av den 27
# december 2022"
CORRIGENDUM_TITLE = (
    "Rättelse till Europaparlamentets och rådets direktiv (EU) 2022/2555 av den "
    "14 december 2022 om åtgärder för en hög gemensam cybersäkerhetsnivå i hela "
    "unionen, om ändring av förordning (EU) nr 910/2014 och direktiv (EU) "
    "2018/1972 och om upphävande av direktiv (EU) 2016/1148 (NIS 2-direktivet) "
    "(Europeiska unionens officiella tidning L 333 av den 27 december 2022)")


def test_corrigendum_reads_the_corrected_acts_name_not_its_oj_coordinate():
    assert short_label(CORRIGENDUM_TITLE) == \
        "(EU) 2022/2555 Rättelse till NIS 2-direktivet"
    assert official_short_title(CORRIGENDUM_TITLE) == \
        "Rättelse till NIS 2-direktivet"


def test_corrigendum_without_a_short_title_keeps_the_subject():
    assert short_label(
        "Rättelse till kommissionens genomförandeförordning (EU) 2022/2105 av "
        "den 29 juli 2022 om fastställande av bestämmelser om kontroller av "
        "överensstämmelse av handelsnormer för olivolja "
        "(Europeiska unionens officiella tidning L 284 av den 4 november 2022)"
    ) == ("(EU) 2022/2105 Rättelse till fastställande av bestämmelser om "
          "kontroller av överensstämmelse av handelsnormer för olivolja")


def test_a_c_series_corrigendum_prints_its_own_number_after_the_coordinate():
    # 12016E/TXTR(04): "… (EUT C 202 av den 7 juni 2016) 2017/C 059/01"
    assert short_label(
        "Rättelse till konsoliderade versioner av fördraget om Europeiska "
        "unionen och fördraget om Europeiska unionens funktionssätt "
        "(Europeiska unionens officiella tidning C 202 av den 7 juni 2016) "
        "2017/C 059/01"
    ) == ("Rättelse till konsoliderade versioner av fördraget om Europeiska "
          "unionen och fördraget om Europeiska unionens funktionssätt")


def test_an_acts_own_oj_note_is_not_mistaken_for_a_corrigendums():
    # the boilerplate strip must not fire on an act that merely ends in a
    # parenthesis of its own
    assert official_short_title(CRA_TITLE) == "Cyberresiliensförordningen"
