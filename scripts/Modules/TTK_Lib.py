from dolphin import gui, memory, utils
from dataclasses import dataclass
from enum import Enum
from typing import Tuple, List, Optional
import math
import os
import zlib
from Modules.framesequence import FrameSequence
from Modules import mkw_classes as classes, TTK_config as config

class ControllerInputType(Enum):
    FACE = 0
    DI = 1
    TRICK = 2

class PlayerType(Enum):
    PLAYER = 0
    GHOST = 1
    
def decodeFaceButton(input):
    A = input % 0x2
    B = (input >> 1) % 0x2
    L = (input >> 2) % 0x2
    
    return [A, B, L]
  
def decodeDirectionInput(input):
    X = input >> 4
    Y = input % 0x10
    
    return [X, Y]
    
def decodeTrickInput(input):
    return input >> 4
    
def encodeFaceButton(A, B, L, prevMask):
    x8Mask = 0x0
    if A and B and prevMask not in (0x0, 0x2, 0x3, 0x7):
        x8Mask = 0x8
    return int(A) + int(B) * 0x2 + int(L) * 0x4 + x8Mask
    
def encodeDirectionInput(X, Y):
    return (X << 4) + Y
    
def encodeTrickInput(input):
    return input * 0x10
    
# Reads binary data in-memory for the specified section
def readRawRKGData(playerType: PlayerType, inputType: ControllerInputType) -> list:
    retList = []
    curAddr = 0x0
    endAddr = 0x0
    addresses = []
    endAddrOffset = 0
    
    # Determine memory region to access
    if (playerType == PlayerType.PLAYER):
        addresses = classes.getInputStorageAddresses()
        endAddrOffset = 0x276C
    else:
        addresses = classes.getGhostAddresses()
        endAddrOffset = memory.read_u32(classes.getGhostAddressLengthPointer()[2])
    
    # Define the address range for the given inputType
    curAddr = addresses[inputType.value]
    if (inputType.value < 2):
        endAddr = addresses[inputType.value + 1]
    else:
        endAddr = curAddr + endAddrOffset
    
    # Begin reading the data
    dataTuple = memory.read_u16(curAddr)
    while True:
        retList.append(dataTuple >> 8)
        retList.append(dataTuple % 0x100)
        curAddr += 0x2
        dataTuple = memory.read_u16(curAddr)
        
        if (dataTuple == 0x0000 or curAddr >= endAddr):
            break
    
    return retList
    
# Expand raw rkg data into a list of frames
def decodeRKGData(data: list, inputType: ControllerInputType) -> List[List[int]]:
    retList = []
    
    if (inputType == ControllerInputType.TRICK):
        trickInput = 0x0
        x100Length = 0x0
        
        for i in range(0, len(data)):
            dataByte = data[i]
            
            if (i %2) == 0:
                trickInput = decodeTrickInput(dataByte)
                x100Length = dataByte % 0x10
            else:
                dataLength = x100Length * 0x100 + dataByte
                retList += [trickInput] * dataLength
    else:
        rawInput = 0x0
        for i in range(0, len(data)):
            dataByte = data[i]
            
            if (i %2) == 0:
                rawInput = dataByte
            else:
                if (inputType == ControllerInputType.FACE):
                    retList += [decodeFaceButton(rawInput)] * dataByte
                else:
                    inputs = decodeDirectionInput(rawInput)
                    retList += [list(map(lambda x: x-7, inputs))] * dataByte
    return retList

# Transform raw RKG data into a FrameSequence
def readFullDecodedRKGData(playerType: PlayerType) -> Optional[FrameSequence]:
    # First make sure we're actually in a race, otherwise we need to bail out
    stage = classes.RaceInfo.stage()
    if (stage == 0):
        gui.add_osd_message("Not in race!")
        return None
    if (stage == 4):
        gui.add_osd_message("Race is over!")
        return None

    # Read each of the input types
    faceData = readRawRKGData(playerType, ControllerInputType.FACE)
    diData = readRawRKGData(playerType, ControllerInputType.DI)
    trickData = readRawRKGData(playerType, ControllerInputType.TRICK)
    if not faceData or not diData or not trickData:
        return None
    
    # Expand into a list where each index is a frame
    faceData = decodeRKGData(faceData, ControllerInputType.FACE)
    diData = decodeRKGData(diData, ControllerInputType.DI)
    trickData = decodeRKGData(trickData, ControllerInputType.TRICK)
    
    # Now transform into a framesequence
    list = [faceData[x] + diData[x] + [trickData[x]] for x in range(len(faceData))]
    sequence = FrameSequence()
    sequence.readFromList(list)
    return sequence

@dataclass
class RKGTuple:
    data: int
    frames: int
    
    def __bytes__(self):
        return bytes([self.data, self.frames])

def encodeTuple(input: int, frames: int, inputType: ControllerInputType) -> RKGTuple:
    if (inputType == ControllerInputType.TRICK):
        return RKGTuple(input + frames >> 8, frames % 0x100)
    else:
        return RKGTuple(input, frames)

def encodeRKGDataType(inputList: FrameSequence,
                      inputType: ControllerInputType) -> List[RKGTuple]:
    retData = []
    prevInput = 0
    bytes = 0
    currFrames = 0
    isFace = (inputType == ControllerInputType.FACE)
    isDI = (inputType == ControllerInputType.DI)
    isTrick = (inputType == ControllerInputType.TRICK)
    
    input = inputList[0]
    if (isFace):
        prevInput = encodeFaceButton(input.accel, input.brake, input.item, 0x0)
    elif (isDI):
        prevInput = encodeDirectionInput(input.stick_x + 7, input.stick_y + 7)
    else:
        prevInput = encodeTrickInput(input.dpad_raw())
    
    for input in inputList:
        currInput = 0
        if (isFace):
            currInput = encodeFaceButton(input.accel, input.brake,
                                         input.item, prevInput)
        elif (isDI):
            currInput = encodeDirectionInput(input.stick_x + 7, input.stick_y + 7)
        else:
            currInput = encodeTrickInput(input.dpad_raw())
        
        frameLimit = 0xFFF if isTrick else 0xFF
        if (prevInput != currInput or currFrames >= frameLimit):
            retData.append(encodeTuple(prevInput, currFrames, inputType))
            currFrames = 1
            bytes += 1
            prevInput = currInput
        else:
            currFrames += 1
    retData.append(encodeTuple(prevInput, currFrames, inputType))
    bytes += 1
    
    return retData

def encodeRKGData(inputList: FrameSequence) -> Tuple[List[int], List[int]]:
    faceTuples = encodeRKGDataType(inputList, ControllerInputType.FACE)
    diTuples = encodeRKGDataType(inputList, ControllerInputType.DI)
    trickTuples = encodeRKGDataType(inputList, ControllerInputType.TRICK)
    
    allTuples = faceTuples+diTuples+trickTuples
    tupleLengths = [len(x) for x in (faceTuples, diTuples, trickTuples)]
    return allTuples, tupleLengths
    
def createRKGFile(input_data: FrameSequence, trackID: int,
                  vehicleID: int, characterID: int, driftID: int) -> bytearray:
    tuples, lengths = encodeRKGData(input_data)
    tuplesFace, tuplesDI, tuplesTrick = lengths
    dataIndex = sum(lengths) * 2
    inputLength = dataIndex + 8
    byteNr8 = (vehicleID << 2) + ((characterID >> 4) & 0x3)
    byteNr9 = (characterID << 4) & 0xFF
    byteNrD = 0x4 + (driftID << 1)
    
    headerData = \
        [0x54, 0xA8, 0x2A, trackID << 2, byteNr8, byteNr9, 0x02, 0x10, 0x00, byteNrD,
        *divmod(inputLength, 0x100) ,0x03, 0x54, 0x00, 0x00, 0x00, 0xA8, 0x00, 0x00,
        0x00, 0x2A, 0x00, 0x00 ,0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0xAA, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xC0, 0x10, 0x00,
        0x54, 0x00, 0x41, 0x00, 0x53, 0x00, 0x54, 0x00, 0x6F, 0x00, 0x6F, 0x00, 0x6C,
        0x00, 0x6B, 0x00, 0x69, 0x00, 0x74, 0x00, 0x22, 0x87, 0x30, 0x89, 0x66, 0xC2,
        0xC4, 0xED, 0xC3, 0x20, 0x44, 0x3C, 0x40, 0x28, 0x38, 0x0C, 0x84, 0x48, 0xCF,
        0x0E, 0x00, 0x08, 0x00, 0xB9, 0x09, 0x00, 0x8A, 0x81, 0x06, 0xC4, 0x10, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7A, 0x6E, *divmod(tuplesFace, 0x100),
        *divmod(tuplesDI, 0x100), *divmod(tuplesTrick, 0x100), 0x00, 0x00]
    
    try:
        idBytes = bytearray("RKGD", "utf-8")
        headerBytes = bytearray(headerData)
        tupleBytes = bytearray()
        for tuple in tuples:
            tupleBytes += bytes(tuple)
        # Pad the rest of the file
        padBytes = bytearray(0x276C - dataIndex)
        
        fileBytes = idBytes + headerBytes + tupleBytes + padBytes
        
        crc = zlib.crc32(fileBytes)
        arg1 = math.floor(crc / 0x1000000)
        arg2 = math.floor((crc & 0x00FF0000) / 0x10000)
        arg3 = math.floor((crc & 0x0000FF00) / 0x100)
        arg4 = math.floor(crc % 0x100)
        
        
        fileBytes += bytearray([arg1, arg2, arg3, arg4])
    
        return fileBytes
    except ValueError:
        gui.add_osd_message("Attempted to parse byte > 0xFF! Aborting RKG write.")
        return bytearray()
    
# This is a tiny helper function that prevents slight repetition in filepath lookups
def writeToCSV(inputs: FrameSequence, playerType: PlayerType) -> None:
    # Get csv file path
    playerStr = "Player" if playerType == PlayerType.PLAYER else "Ghost"
    relativePath = config.textFilePath(playerStr)
    absolutePath = os.path.join(os.getcwd(), "User/Load/Scripts/", relativePath)
    
    # Write to csv, error if cannot write
    if inputs.writeToFile(absolutePath):
        gui.add_osd_message("{} inputs written to {}".format(playerStr, relativePath))
    else:
        gui.add_osd_message(
            "{} is currently locked by another program.".format(relativePath)
        )
        
def writeToBackupCSV(inputs: FrameSequence, backupNumber: int) -> None:
    relativePath = config.textFilePath("Backup")
    relativePath = relativePath.replace("##", "{:02d}".format(backupNumber))
    inputs.writeToFile(os.path.join(os.getcwd(), "User/Load/Scripts/", relativePath))
        
def getMetadataAndWriteToRKG(inputs: FrameSequence, playerType: PlayerType) -> None:
    # Get metadata
    trackID = classes.RaceDataSettings.course_id()
    vehicleID = classes.RaceDataPlayer.vehicle_id(playerType.value)
    characterID = classes.RaceDataPlayer.character_id(playerType.value)
    driftID = classes.InputMgr.drift_id(playerType.value)
    
    # Get bytes to write
    fileBytes = createRKGFile(inputs, trackID, vehicleID, characterID, driftID)
    
    if (len(fileBytes)):
        # Write bytes to appropriate file
        writeToRKG(fileBytes, playerType)
    else:
        gui.add_osd_message("No bytes to write to RKG file.")
        
def writeToRKG(fileBytes: bytearray, playerType: PlayerType) -> None:
    # Get csv file path
    playerStr = "Player" if playerType == PlayerType.PLAYER else "Ghost"
    relativePath = config.rkgFilePath[playerStr]
    absolutePath = os.path.join(os.getcwd(), "User/Load/Scripts/", relativePath)
    
    try:
        with open(absolutePath, "wb") as f:
            f.write(fileBytes)
        gui.add_osd_message("{} inputs written to {}".format(playerStr, relativePath))
    except IOError:
        gui.add_osd_message(
            "{} is currently locked by another program.".format(relativePath)
        )
        
def getInputSequenceFromCSV(playerType: PlayerType) -> FrameSequence:
    # Get csv file path
    playerStr = "Player" if playerType == PlayerType.PLAYER else "Ghost"
    relativePath = config.textFilePath(playerStr)
    absolutePath = os.path.join(os.getcwd(), "User/Load/Scripts/", relativePath)
    
    # Get the frame sequence
    return FrameSequence(absolutePath)

def getDBS():
    address = {"RMCE01": 0x8051c8d8, "RMCP01": 0x80520d4c,
               "RMCJ01": 0x805206cc, "RMCK01": 0x8050ed70}
    return address[utils.get_game_id()]
def getFBS():
    address = {"RMCE01": 0x8051eacc, "RMCP01": 0x80522f40,
               "RMCJ01": 0x805228c0, "RMCK01": 0x80510f64}
    return address[utils.get_game_id()]
def getTBS():
    address = {"RMCE01": 0x8051e7e8, "RMCP01": 0x80522c5c,
               "RMCJ01": 0x805225dc, "RMCK01": 0x80510c80}
    return address[utils.get_game_id()]
    
dbs = getDBS()
fbs = getFBS()
tbs = getTBS()

def writeGhostInputs(inputs: FrameSequence) -> None:
    # DirectionButtonsStream_readFrame
    # lbz r3, 0x12 (r3)
    # blr
    memory.write_u32(dbs, 0x88630012)
    memory.write_u32(dbs + 0x4, 0x4e800020)
    memory.invalidate_icache(dbs, 0x8)
    # FaceButtonsStream_readFrame
    # lbz r3, 0x12 (r3)
    # blr
    memory.write_u32(fbs, 0x88630012)
    memory.write_u32(fbs + 0x4, 0x4e800020)
    memory.invalidate_icache(fbs, 0x8)
    # TricksButtonStream_readFrame
    # lbz r3, 0x12 (r3)
    # blr
    memory.write_u32(tbs, 0x88630012)
    memory.write_u32(tbs + 0x4, 0x4e800020)
    memory.invalidate_icache(tbs, 0x8)
    
    set_ghost_buttons(inputs)

# Restore instructions if no inputs
def stopWriteGhostInputs() -> None:
    # DirectionButtonsStream_readFrame
    # stwu sp, -0x20 (sp)
    # mflr r0
    memory.write_u32(dbs, 0x9421ffe0)
    memory.write_u32(dbs + 0x4, 0x7c0802a6)
    memory.invalidate_icache(dbs, 0x8)
    # FaceButtonsStream_readFrame
    # stwu sp, -0x20 (sp)
    # mflr r0
    memory.write_u32(fbs, 0x9421ffe0)
    memory.write_u32(fbs + 0x4, 0x7c0802a6)
    memory.invalidate_icache(fbs, 0x8)
    # TricksButtonStream_readFrame
    # stwu sp, -0x20 (sp)
    # mflr r0
    memory.write_u32(tbs, 0x9421ffe0)
    memory.write_u32(tbs + 0x4, 0x7c0802a6)
    memory.invalidate_icache(tbs, 0x8)
    
def set_ghost_buttons(inputs):
    # NOTE: Ghost controller index 1 is consistent in the base game
    ghost_controller = memory.read_u32(classes.InputMgr.chain()) + 0x3f08

    buttons = memory.read_u32(ghost_controller + 0x94)
    memory.write_u8(buttons + 0x12, inputs.accel + (inputs.brake << 1) +
                    (inputs.item << 2) | ((inputs.accel & inputs.brake) << 3))

    stick = memory.read_u32(ghost_controller + 0x98)
    memory.write_u8(stick + 0x12, (inputs.stick_y + 7)
                    | ((inputs.stick_x + 7) << 4))

    trickbuttons = memory.read_u32(ghost_controller + 0x9C)
    memory.write_u8(trickbuttons + 0x12, inputs.dpad_raw())

