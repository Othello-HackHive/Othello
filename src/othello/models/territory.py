from pydantic import BaseModel, ConfigDict


class Territory(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    territory_id: str
    room_ids: list[str]
    node_ids: list[str]
    area_m2: float
    hub_zone_m: object


class PartitionOption(BaseModel):
    hub_count: int
    territories: list[Territory]
    area_gap_m2: float
