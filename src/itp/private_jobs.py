"""Existing try-on/FaceVerse workflows, with documents kept only in RAM."""

from itp.face_refine import FaceRefineStore
from itp.transient import TransientDocuments
from itp.tryon import TryOnStore


class PrivateTryOnStore(TransientDocuments):
    create = TryOnStore.create


class PrivateFaceStore(TransientDocuments):
    create = FaceRefineStore.create
