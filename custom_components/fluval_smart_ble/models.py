"""Known Fluval Smart light models.

The two-byte model ID is broadcast by the light itself in the BLE
advertisement's manufacturer data (big-endian), which is how the official
FluvalSmart app recognizes a device before ever connecting to it. The IDs
and channel layouts below were extracted from the app's DeviceUtil class.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FluvalModel:
    """Static description of a Fluval Smart light model."""

    model_id: int
    name: str
    channels: tuple[str, ...]
    is_rgbw: bool = False


# Channel layouts reused across several product lines.
_CHANNELS_RGBW = ("Red", "Green", "Blue", "White")
_CHANNELS_MARINE = ("Pink", "Cyan", "Blue", "Purple", "Cold White")
_CHANNELS_FRESH = ("Pink", "Blue", "Cold White", "Pure White", "Warm White")

MODELS: dict[int, FluvalModel] = {}


def _add(model_id: int, name: str, channels: tuple[str, ...], is_rgbw: bool = False) -> None:
    MODELS[model_id] = FluvalModel(model_id, name, channels, is_rgbw)


# Marine & Reef
_add(289, "Marine & Reef 500mm", _CHANNELS_MARINE)
_add(290, "Marine & Reef 800mm", _CHANNELS_MARINE)
_add(291, "Marine & Reef 1100mm", _CHANNELS_MARINE)
_add(292, "Marine & Reef 1000mm", _CHANNELS_MARINE)
_add(293, "Marine & Reef 380mm", _CHANNELS_MARINE)
_add(294, "Marine & Reef 750mm", _CHANNELS_MARINE)

# Fresh & Plant
_add(305, "Fresh & Plant 500mm", _CHANNELS_FRESH)
_add(306, "Fresh & Plant 800mm", _CHANNELS_FRESH)
_add(307, "Fresh & Plant 1100mm", _CHANNELS_FRESH)
_add(308, "Fresh & Plant 1000mm", _CHANNELS_FRESH)
_add(309, "Fresh & Plant 380mm", _CHANNELS_FRESH)
_add(310, "Fresh & Plant 600mm", _CHANNELS_FRESH)
_add(311, "Fresh & Plant 900mm", _CHANNELS_FRESH)

# AquaSky (RGBW)
_add(321, "Aquasky 600mm", _CHANNELS_RGBW, is_rgbw=True)
_add(322, "Aquasky 900mm", _CHANNELS_RGBW, is_rgbw=True)
_add(323, "Aquasky 1200mm", _CHANNELS_RGBW, is_rgbw=True)
_add(324, "Aquasky 380mm", _CHANNELS_RGBW, is_rgbw=True)
_add(325, "Aquasky 530mm", _CHANNELS_RGBW, is_rgbw=True)
_add(326, "Aquasky 835mm", _CHANNELS_RGBW, is_rgbw=True)
_add(327, "Aquasky 990mm", _CHANNELS_RGBW, is_rgbw=True)
_add(328, "Aquasky 750mm", _CHANNELS_RGBW, is_rgbw=True)
_add(329, "Aquasky 1150mm", _CHANNELS_RGBW, is_rgbw=True)
_add(336, "Aquasky 910mm", _CHANNELS_RGBW, is_rgbw=True)

# Nano
_add(337, "Wing Nano Marine", _CHANNELS_MARINE)
_add(338, "Wing Nano Fresh", _CHANNELS_MARINE)

# Roma (RGBW)
_add(369, "Roma90", _CHANNELS_RGBW, is_rgbw=True)
_add(370, "Roma125", _CHANNELS_RGBW, is_rgbw=True)
_add(371, "Roma200", _CHANNELS_RGBW, is_rgbw=True)
_add(372, "Roma240", _CHANNELS_RGBW, is_rgbw=True)

# Vicenza / Venezia
_add(373, "Vicenza 180", _CHANNELS_MARINE)
_add(374, "Vicenza 260", _CHANNELS_MARINE)
_add(375, "Venezia 190", _CHANNELS_MARINE)
_add(376, "Venezia 350A", _CHANNELS_MARINE)
_add(377, "Venezia 350B", _CHANNELS_MARINE)

# A-Sky / Oak (RGBW)
_add(384, "A-Sky Aqua 679mm", _CHANNELS_RGBW, is_rgbw=True)
_add(385, "A-Sky Aqua 1025mm", _CHANNELS_RGBW, is_rgbw=True)

# Plant Aqua
_add(386, "Plant Aqua 529mm", _CHANNELS_MARINE)
_add(387, "Plant Aqua 875mm", _CHANNELS_MARINE)
_add(388, "Plant Aqua 1075mm", _CHANNELS_MARINE)


def get_model(model_id: int) -> FluvalModel | None:
    """Look up a known model by its advertised ID, if any."""
    return MODELS.get(model_id)


# Fallback used when a device advertises an unrecognized model ID: a
# generic 4-channel RGBW light, which matches the most common layout.
GENERIC_MODEL = FluvalModel(0, "Fluval Smart Light", _CHANNELS_RGBW, is_rgbw=True)
