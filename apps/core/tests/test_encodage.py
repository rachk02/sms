import pytest

from apps.core.encodage import ALPHABET_GSM, EXTENSION_GSM, Encodage, analyser


def test_160_caracteres_gsm_tiennent_en_un_segment():
    analyse = analyser("a" * 160)
    assert (analyse.encodage, analyse.unites, analyse.segments) == (Encodage.GSM7, 160, 1)


def test_161_caracteres_gsm_donnent_deux_segments_de_153():
    assert analyser("a" * 161).segments == 2
    assert analyser("a" * 306).segments == 2
    assert analyser("a" * 307).segments == 3


def test_un_e_accent_circonflexe_bascule_en_ucs2_et_tombe_a_70():
    texte = "ê" + "a" * 69
    analyse = analyser(texte)
    assert analyse.encodage == Encodage.UCS2
    assert analyse.segments == 1
    assert analyse.hors_gsm == ["ê"]


def test_71_caracteres_ucs2_donnent_deux_segments_de_67():
    assert analyser("ê" * 71).segments == 2
    assert analyser("ê" * 134).segments == 2
    assert analyser("ê" * 135).segments == 3


@pytest.mark.parametrize("caractere", ["€", "\\"])
def test_l_euro_et_la_barre_oblique_inverse_comptent_double(caractere):
    analyse = analyser(caractere)
    assert (analyse.encodage, analyse.unites) == (Encodage.GSM7, 2)
    assert analyser(caractere * 80).segments == 1
    assert analyser("a" + caractere * 80).segments == 2


@pytest.mark.parametrize("accent", list("éèàùìòäöüñÇßæ"))
def test_les_accents_de_la_table_gsm_ne_basculent_pas_en_ucs2(accent):
    analyse = analyser(f"Bonjour {accent}")
    assert analyse.encodage == Encodage.GSM7
    assert analyse.hors_gsm == []


@pytest.mark.parametrize(
    "couteux", ["ê", "â", "î", "ô", "û", "ë", "ï", "ç", "œ", "’", "“", "—", "…"]
)
def test_les_caracteres_couteux_de_la_charte_basculent_en_ucs2(couteux):
    assert analyser(f"Bonjour {couteux}").encodage == Encodage.UCS2


def test_un_caractere_d_extension_n_est_jamais_coupe_entre_deux_segments():
    # 152 unités puis un euro : l'euro ne tient plus dans le premier segment.
    texte = "a" * 152 + "€" + "a" * 10
    analyse = analyser(texte)
    assert analyse.unites == 164
    assert analyse.segments == 2
    # Le second segment accueille 76 euros, soit 152 unités sur 153.
    assert analyser("a" * 152 + "€" * 76).segments == 2
    assert analyser("a" * 152 + "€" * 77).segments == 3


def test_un_emoji_compte_deux_unites_et_n_est_jamais_coupe():
    assert analyser("\U0001f600").unites == 2
    assert analyser("\U0001f600" * 35).segments == 1
    # 66 unités puis un emoji de deux unités : il passe au segment suivant.
    assert analyser("ê" * 66 + "\U0001f600" + "ê" * 66).segments == 3


def test_le_caractere_d_echappement_n_est_pas_un_caractere_gsm_utilisable():
    assert "\x1b" not in ALPHABET_GSM and "\x1b" not in EXTENSION_GSM
    assert analyser("\x1b").encodage == Encodage.UCS2


def test_l_alphabet_compte_les_128_positions_moins_l_echappement():
    assert len(ALPHABET_GSM) == 127
    assert len(EXTENSION_GSM) == 10
