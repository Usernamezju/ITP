from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="未命名资产", min_length=1, max_length=80)
    front: str = Field(pattern=r"^[a-f0-9]{32}$")
    views: dict[
        Literal["left", "right", "back", "left_front", "right_front"],
        Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")],
    ] = Field(default_factory=dict)
    views_consistent_confirmed: bool = False
    pose_mode: Literal["original", "custom", "a-pose", "t-pose"] = "original"
    pose_reference: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    face_count: int = Field(default=100000, ge=3000, le=1500000)
    seed: int = Field(default=42, ge=0, le=2147483647)
    # Retopology, PBR texturing, rigging and FBX export were removed with the
    # rest of the post-processing: the pipeline now produces one previewable
    # GLB.  The four flags stay declared — and can only ever be false — so a
    # client built before the change keeps getting a clear refusal instead of a
    # validation error it cannot explain.
    topology: bool = False
    texture: bool = False
    rig: bool = False
    export_fbx: bool = False

    @model_validator(mode="after")
    def validate_combination(self):
        if self.topology or self.texture or self.rig or self.export_fbx:
            raise ValueError("基础建模仅包含姿势编辑与几何生成，不支持拓扑、PBR、绑骨或 FBX")
        if self.pose_mode == "custom" and not self.pose_reference:
            raise ValueError("自定义姿势需要姿势参考图")
        if self.pose_mode != "custom" and self.pose_reference:
            raise ValueError("只有自定义姿势模式接受姿势参考图")
        if self.pose_mode != "original" and self.views:
            raise ValueError("姿势变换不能混用原姿势的多视角图片")
        if self.views and not self.views_consistent_confirmed:
            raise ValueError("请确认所有视角为同一人物、同一服装和同一姿势")
        return self
