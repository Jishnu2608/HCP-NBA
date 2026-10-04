from pydantic import BaseModel, ConfigDict


class StrictBody(BaseModel):
    """Base for every request body: a field the endpoint does not define is an error, so a
    client cannot slip in `role`, `user_id`, `professionally_verified` or similar and hope
    it is used. The server never reads such values from a request in any case."""

    model_config = ConfigDict(extra="forbid", str_max_length=4000)
