"""Registry of domain materializers by entity."""

from __future__ import annotations

from .materialization import EntityMaterializer
from .pcr.materializer import PCR_MATERIALIZER
from .st.materializer import ST_MATERIALIZER

ENTITY_MATERIALIZERS: dict[str, EntityMaterializer] = {
    PCR_MATERIALIZER.entity_name: PCR_MATERIALIZER,
    ST_MATERIALIZER.entity_name: ST_MATERIALIZER,
}


def get_entity_materializer(entity_name: str) -> EntityMaterializer | None:
    """Return the registered materializer using case-insensitive matching."""

    return ENTITY_MATERIALIZERS.get(entity_name.casefold())
