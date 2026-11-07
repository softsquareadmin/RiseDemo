from fastapi import APIRouter, HTTPException, Request

from app.models.configuration import LANGUAGES, Configuration

router = APIRouter(prefix="/api/configurations", tags=["configurations"])


def _store(request: Request):
    return request.app.state.configs


def _unique_id(store, base: str) -> str:
    cid, n = base, 2
    while store.get(cid):
        cid = f"{base}_{n}"
        n += 1
    return cid


@router.get("")
def list_configs(request: Request):
    return {"configurations": _store(request).list(), "languages": LANGUAGES}


@router.post("", status_code=201)
def create_config(cfg: Configuration, request: Request):
    store = _store(request)
    cfg.id = _unique_id(store, cfg.id)
    data = cfg.model_dump()
    store.save(data)
    return data


@router.get("/{cid}")
def get_config(cid: str, request: Request):
    cfg = _store(request).get(cid)
    if not cfg:
        raise HTTPException(404, "Configuration not found")
    return cfg


@router.put("/{cid}")
def update_config(cid: str, cfg: Configuration, request: Request):
    store = _store(request)
    if not store.get(cid):
        raise HTTPException(404, "Configuration not found")
    cfg.id = cid
    data = cfg.model_dump()
    store.save(data)  # sessions keep their own snapshot, so history is unaffected
    return data


@router.post("/{cid}/duplicate", status_code=201)
def duplicate_config(cid: str, request: Request):
    store = _store(request)
    src = store.get(cid)
    if not src:
        raise HTTPException(404, "Configuration not found")
    copy = Configuration.model_validate({**src, "id": "", "name": f"{src['name']} (Copy)"})
    copy.id = _unique_id(store, copy.id)
    data = copy.model_dump()
    store.save(data)
    return data


@router.delete("/{cid}")
def delete_config(cid: str, request: Request):
    if not _store(request).delete(cid):
        raise HTTPException(404, "Configuration not found")
    return {"deleted": cid}
