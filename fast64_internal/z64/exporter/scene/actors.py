import bpy

from dataclasses import dataclass, field
from typing import Optional
from mathutils import Matrix
from bpy.types import Object

from ....utility import PluginError, CData, hexOrDecInt, indent
from ....game_data import game_data
from ...utility import getObjectList, getEvalParams
from ...actor.properties import OOTActorProperty
from ..utility import Utility
from ..actor import Actor


@dataclass(unsafe_hash=True)
class TransitionActor(Actor):
    """Defines a Transition Actor"""

    isRoomTransition: Optional[bool] = field(init=False, default=None)
    roomFrom: Optional[int] = field(init=False, default=None)
    roomTo: Optional[int] = field(init=False, default=None)
    cameraFront: Optional[str] = field(init=False, default=None)
    cameraBack: Optional[str] = field(init=False, default=None)

    def getEntryC(self):
        """Returns a single transition actor entry"""

        sides = [(self.roomFrom, self.cameraFront), (self.roomTo, self.cameraBack)]
        roomData = "{ " + ", ".join(f"{room}, {cam}" for room, cam in sides) + " }"
        posData = "{ " + ", ".join(f"{round(pos)}" for pos in self.pos) + " }"

        actorInfos = [roomData, self.id, posData, self.rot, self.params]
        infoDescs = ["Room & Cam Index (Front, Back)", "Actor ID", "Position", "Rotation Y", "Parameters"]

        return (
            (indent + f"// {self.name}\n" + indent if self.name != "" else "")
            + "{\n"
            + ",\n".join((indent * 2) + f"/* {desc:30} */ {info}" for desc, info in zip(infoDescs, actorInfos))
            + ("\n" + indent + "},\n")
        )


@dataclass
class SceneTransitionActors:
    name: str
    entries: list[TransitionActor]

    @staticmethod
    def new(name: str, sceneObj: Object, transform: Matrix, headerIndex: int):
        # we need to get the corresponding room index if a transition actor
        # do not change rooms
        roomObjList = getObjectList(sceneObj.children_recursive, "EMPTY", "Room")
        actorToRoom: dict[Object, Object] = {}
        for obj in roomObjList:
            for childObj in obj.children_recursive:
                if childObj.type == "EMPTY" and childObj.ootEmptyType == "Transition Actor":
                    actorToRoom[childObj] = obj

        actorObjList = getObjectList(sceneObj.children_recursive, "EMPTY", "Transition Actor")
        actorObjList.sort(key=lambda obj: actorToRoom[obj].ootRoomHeader.roomIndex)

        entries: list[TransitionActor] = []
        for obj in actorObjList:
            transActorProp = obj.ootTransitionActorProperty
            actorProp: OOTActorProperty = transActorProp.actor
            if Utility.isCurrentHeaderValid(actorProp.headerSettings, headerIndex) and actorProp.actor_id != "None":
                pos, rot, _, _ = Utility.getConvertedTransform(transform, sceneObj, obj, True)
                transActor = TransitionActor()

                if transActorProp.isRoomTransition:
                    if transActorProp.fromRoom is None or transActorProp.toRoom is None:
                        raise PluginError("ERROR: Missing room empty object assigned to transition.")
                    fromIndex = transActorProp.fromRoom.ootRoomHeader.roomIndex
                    toIndex = transActorProp.toRoom.ootRoomHeader.roomIndex
                else:
                    fromIndex = toIndex = actorToRoom[obj].ootRoomHeader.roomIndex
                front = (fromIndex, Utility.getPropValue(transActorProp, "cameraTransitionFront"))
                back = (toIndex, Utility.getPropValue(transActorProp, "cameraTransitionBack"))

                if actorProp.actor_id == "Custom":
                    transActor.id = actorProp.actor_id_custom
                else:
                    transActor.id = actorProp.actor_id

                transActor.name = (
                    game_data.z64.actors.actorsByID[actorProp.actor_id].name.replace(
                        f" - {actorProp.actor_id.removeprefix('ACTOR_')}", ""
                    )
                    if actorProp.actor_id != "Custom"
                    else "Custom Actor"
                )

                transActor.pos = pos
                transActor.rot = f"DEG_TO_BINANG({(rot[1] * (180 / 0x8000)):.3f})"  # TODO: Correct axis?
                transActor.params = (
                    actorProp.params
                    if bpy.context.scene.fast64.oot.use_new_actor_panel and actorProp.actor_id != "Custom"
                    else actorProp.params_custom
                )
                transActor.roomFrom, transActor.cameraFront = front
                transActor.roomTo, transActor.cameraBack = back
                entries.append(transActor)
        return SceneTransitionActors(name, entries)

    @staticmethod
    def from_data(raw_data: str, not_zapd_assets: bool):
        actor_list = []

        if not_zapd_assets:
            entries = raw_data.removeprefix("{").removesuffix(",},").split(",},{")
        else:
            entries = raw_data.split("},")

        for entry in entries:
            if entry == "":
                continue

            params = entry.replace("{", "").replace("}", "").split(",")

            # trailing commas
            for p in params:
                if p == "":
                    params.remove(p)

            assert len(params) == 10
            trans_actor = TransitionActor()
            trans_actor.name = "(unset)"
            trans_actor.id = params[4]
            trans_actor.pos = [hexOrDecInt(params[5]), hexOrDecInt(params[6]), hexOrDecInt(params[7])]
            trans_actor.rot = getEvalParams(params[8]) if "DEG_TO_BINANG" in params[8] else params[8]
            trans_actor.params = params[9]
            trans_actor.roomFrom = hexOrDecInt(params[0])
            trans_actor.roomTo = hexOrDecInt(params[2])
            trans_actor.isRoomTransition = trans_actor.roomFrom != trans_actor.roomTo
            trans_actor.cameraFront = params[1]
            trans_actor.cameraBack = params[3]
            actor_list.append(trans_actor)

        return SceneTransitionActors("(unset)", actor_list)

    def getCmd(self):
        """Returns the transition actor list scene command"""

        return indent + f"SCENE_CMD_TRANSITION_ACTOR_LIST({len(self.entries)}, {self.name}),\n"

    def getC(self):
        """Returns the transition actor array"""

        transActorList = CData()
        listName = f"TransitionActorEntry {self.name}"

        # .h
        transActorList.header = f"extern {listName}[];\n"

        # .c
        transActorList.source = (
            (f"{listName}[]" + " = {\n") + "\n".join(transActor.getEntryC() for transActor in self.entries) + "};\n\n"
        )

        return transActorList


@dataclass
class EntranceActor(Actor):
    """Defines an Entrance Actor"""

    roomIndex: Optional[int] = field(init=False, default=None)
    spawnIndex: Optional[int] = field(init=False, default=None)

    def getEntryC(self):
        """Returns a single spawn entry"""

        return indent + "{ " + f"{self.spawnIndex}, {self.roomIndex}" + " },\n"


@dataclass
class SceneEntranceActors:
    name: str
    entries: list[EntranceActor]

    @staticmethod
    def new(name: str, sceneObj: Object, transform: Matrix, headerIndex: int):
        """Returns the entrance actor list based on empty objects with the type 'Entrance'"""

        entranceActorFromIndex: dict[int, EntranceActor] = {}
        actorObjList = getObjectList(sceneObj.children_recursive, "EMPTY", "Entrance")
        for obj in actorObjList:
            entranceProp = obj.ootEntranceProperty
            actorProp: OOTActorProperty = entranceProp.actor
            if Utility.isCurrentHeaderValid(actorProp.headerSettings, headerIndex) and actorProp.actor_id != "None":
                pos, rot, _, _ = Utility.getConvertedTransform(transform, sceneObj, obj, True)
                entranceActor = EntranceActor()

                entranceActor.name = (
                    game_data.z64.actors.actorsByID[actorProp.actor_id].name.replace(
                        f" - {actorProp.actor_id.removeprefix('ACTOR_')}", ""
                    )
                    if actorProp.actor_id != "Custom"
                    else "Custom Actor"
                )

                entranceActor.id = "ACTOR_PLAYER" if not entranceProp.customActor else actorProp.actor_id_custom
                entranceActor.pos = pos
                entranceActor.rot = ", ".join(f"DEG_TO_BINANG({(r * (180 / 0x8000)):.3f})" for r in rot)
                entranceActor.params = (
                    actorProp.params
                    if bpy.context.scene.fast64.oot.use_new_actor_panel and not entranceProp.customActor
                    else actorProp.params_custom
                )
                if entranceProp.tiedRoom is not None:
                    entranceActor.roomIndex = entranceProp.tiedRoom.ootRoomHeader.roomIndex
                else:
                    raise PluginError("ERROR: Missing room empty object assigned to the entrance.")
                entranceActor.spawnIndex = entranceProp.spawnIndex

                if entranceProp.spawnIndex not in entranceActorFromIndex:
                    entranceActorFromIndex[entranceProp.spawnIndex] = entranceActor
                else:
                    raise PluginError(f"ERROR: Repeated Spawn Index: {entranceProp.spawnIndex}")

        entranceActorFromIndex = dict(sorted(entranceActorFromIndex.items()))
        if list(entranceActorFromIndex.keys()) != list(range(len(entranceActorFromIndex))):
            raise PluginError("ERROR: The spawn indices are not consecutive!")

        return SceneEntranceActors(name, list(entranceActorFromIndex.values()))

    def getCmd(self):
        """Returns the spawn list scene command"""

        name = self.name if len(self.entries) > 0 else "NULL"
        return indent + f"SCENE_CMD_SPAWN_LIST({len(self.entries)}, {name}),\n"

    def getC(self):
        """Returns the spawn actor array"""

        spawnActorList = CData()
        listName = f"ActorEntry {self.name}"

        # .h
        spawnActorList.header = f"extern {listName}[];\n"

        # .c
        spawnActorList.source = (
            (f"{listName}[]" + " = {\n") + "".join(entrance.getActorEntry() for entrance in self.entries) + "};\n\n"
        )

        return spawnActorList


@dataclass
class SceneSpawns(Utility):
    """This class handles scene actors (transition actors and entrance actors)"""

    name: str
    entries: list[EntranceActor]

    def getCmd(self):
        """Returns the entrance list scene command"""

        return indent + f"SCENE_CMD_ENTRANCE_LIST({self.name if len(self.entries) > 0 else 'NULL'}),\n"

    def getC(self):
        """Returns the spawn array"""

        spawnList = CData()
        listName = f"Spawn {self.name}"

        # .h
        spawnList.header = f"extern {listName}[];\n"

        # .c
        spawnList.source = (
            (f"{listName}[]" + " = {\n")
            + (indent + "// { Spawn Actor List Index, Room Index }\n")
            + "".join(entrance.getEntryC() for entrance in self.entries)
            + "};\n\n"
        )

        return spawnList
