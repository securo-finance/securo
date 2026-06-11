"""Static Pluggy lookup tables, kept out of the provider logic.

The COMPE map lets us recover a real bank logo when a Pluggy *connector*
doesn't expose one (notably the demo "MeuPluggy" connector). Pluggy still
embeds the real bank in each account's ``bankData.transferNumber`` — the
3-digit prefix is the Brazilian Central Bank COMPE code — so we map that code
to the bank's actual Pluggy CDN icon.

Icon filenames were harvested from Pluggy's own ``/connectors`` and do NOT
track the connector id (e.g. Nubank is connector 612 but its icon is
``212.svg``), which is why an explicit table is needed.
"""
from typing import Optional

# Public Pluggy CDN that hosts the per-bank connector icons.
PLUGGY_ICON_BASE = "https://cdn.pluggy.ai/assets/connector-icons"

# Brazilian COMPE (3-digit bank) code -> Pluggy connector-icon filename.
# Ordered by code; covers the popular retail/digital banks plus the larger
# regionals and cooperatives. Unknown codes fall back to the account-type icon.
COMPE_TO_PLUGGY_ICON = {
    "001": "211.svg",  # Banco do Brasil
    "003": "679.svg",  # Banco da Amazônia
    "004": "671.svg",  # Banco do Nordeste do Brasil
    "021": "681.svg",  # Banestes
    "033": "208.svg",  # Santander
    "041": "659.svg",  # Banrisul
    "047": "735.svg",  # Banese
    "070": "682.svg",  # BRB - Banco de Brasília
    "077": "215.svg",  # Banco Inter
    "082": "734.svg",  # Banco Topázio
    "085": "224.svg",  # Ailos
    "104": "219.svg",  # Caixa Econômica Federal
    "133": "684.svg",  # Cresol
    "136": "663.svg",  # Unicred
    "208": "214.svg",  # BTG Pactual
    "212": "654.svg",  # Banco Original
    "213": "738.svg",  # Banco Arbi
    "218": "723.svg",  # Banco BS2
    "237": "203.svg",  # Bradesco
    "243": "680.svg",  # Banco Master
    "260": "212.svg",  # Nubank (Nu Pagamentos)
    "290": "692.svg",  # PagBank (PagSeguro)
    "318": "652.svg",  # Banco BMG
    "323": "206.svg",  # Mercado Pago
    "330": "739.svg",  # Banco Bari
    "335": "653.svg",  # Banco Digio
    "336": "726.svg",  # C6 Bank
    "341": "201.svg",  # Itaú
    "348": "202.svg",  # Banco XP
    "364": "686.svg",  # Efí (Gerencianet)
    "376": "757.svg",  # JP Morgan
    "380": "651.svg",  # PicPay
    "389": "742.svg",  # Banco Mercantil do Brasil
    "403": "250.svg",  # Cora
    "422": "629.svg",  # Safra
    "604": "731.svg",  # Banco Industrial do Brasil
    "623": "657.svg",  # Banco PAN
    "633": "769.svg",  # Banco Rendimento
    "637": "714.svg",  # Banco Sofisa
    "654": "740.svg",  # Banco Digimais
    "707": "685.svg",  # Banco Daycoval
    "735": "689.svg",  # Banco Neon
    "748": "661.svg",  # Sicredi
    "756": "228.svg",  # Sicoob
}


def pluggy_icon_for_compe(compe: Optional[str]) -> Optional[str]:
    """Full Pluggy CDN icon URL for a COMPE code, or None if unmapped."""
    icon = COMPE_TO_PLUGGY_ICON.get(compe) if compe else None
    return f"{PLUGGY_ICON_BASE}/{icon}" if icon else None
