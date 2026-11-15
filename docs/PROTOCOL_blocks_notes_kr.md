# BLOCKS Protocol Notes

Python 포트의 명세서. 1~6장은 SDK 헤더에서 도출한 와이어 포맷, 7~10장은 디바이스 모델/LED 제어, 11장은 실디바이스 검증으로 발견한 펌웨어 동작 (SDK 헤더에 없음).
SDK 안의 `protocol/Protocol Format.txt` 는 오래되어 일부 부정확함 — 이 문서는 코드 + 실측이 진실.

## 1. SysEx 프레임 구조

```
F0 00 21 10 77 [devIdx]  [ ... payload bytes ... ]  [checksum]  F7
└─ 5 byte header ─┘  └ 1 ┘  └────── M bytes ──────┘  └── 1 ──┘ └ 1 ┘
```

- ROLI manufacturer id: `00 21 10`, sub-id: `77` (`roliSysexHeader`)
- `devIdx` 바이트:
  - `bit 6 (0x40)` = direction flag. **set** → device→host, **clear** → host→device.
  - `bits 0..5` = topology index of device (0..63). 0 = master.
  - `bit 7 (0x80)` 은 항상 0 (SysEx 데이터 바이트라서)
  - `topologyIndexForBroadcast = 63` — 모든 디바이스에 보낼 때
- payload 바이트들(M개): MSB가 항상 0인 7-bit 바이트들. 비트스트림이 패킹되어 들어 있음.
- `checksum`: `payload[0..M-1]` 위에서 계산된 1바이트 (아래 알고리즘).
- `F7` 종결.

### 체크섬 알고리즘

```python
def checksum(payload: bytes) -> int:
    c = len(payload) & 0xFF
    for b in payload:
        c = (c + (c * 2 + b)) & 0xFF
    return c & 0x7F
```

검증 시: `checksum(payload) == sysex[-2]`. checksum 계산은 sysex 헤더와 devIdx 를 **포함하지 않음** — payload만.

## 2. 7-bit 비트스트림 패킹

SysEx는 바이트당 최상위 비트가 0이어야 하므로 데이터 바이트당 7비트만 사용 가능. SDK는 임의 비트 폭 정수들을 LSB-first로 7-bit 바이트들에 채워 넣음.

### writer (`Packed7BitArrayBuilder.writeBits`)
- 현재 바이트의 비어 있는 비트 수만큼 채우고 다음 바이트로 넘어감
- 한 정수가 7비트보다 크면 LSB 7비트씩 잘라 여러 바이트에 걸쳐 저장
- 정수는 byte 안에서 **LSB가 낮은 비트 위치**에 들어감 (little-endian within-byte)

### reader (`Packed7BitArrayReader.readBits`)
- 현재 바이트에서 `bitsReadInCurrentByte` 위치부터 LSB 방향으로 읽음
- 필요한 비트가 현재 바이트에 부족하면 다음 바이트로 넘어가 누적

Python 구현 메모: `int.from_bytes` 같은 표준 함수로 안 됨. 커스텀 reader/writer 클래스 필요. 단위 테스트는 builder→reader round-trip으로 검증.

## 3. 패킷 페이로드 레이아웃

### Device → Host (devIdx의 0x40 비트 set)

페이로드는 다음 비트스트림:
```
[ PacketTimestamp : 32 ]              # 디바이스 부팅 후 ms (절대 타임스탬프)
[ Message1 ][ Message2 ] ... [ 0 ]    # MessageType=0 이 종결
```

각 메시지: `MessageType (7 bits)` + 메시지별 본문.

### Host → Device (devIdx의 0x40 비트 clear)

**PacketTimestamp 없음**. 헤더 직후 바로 메시지가 시작됨. 메시지 하나씩 보내는 게 일반적.

```
[ MessageType (7) ][ message body ]
```

## 4. Device → Host 메시지 카탈로그

`MessageFromDevice` enum (모든 타입은 7비트):

| Hex  | 이름                       | 본문 비트 레이아웃                                                                 |
|------|---------------------------|-------------------------------------------------------------------------------|
| 0x01 | deviceTopology (begin)    | `ProtocolVersion(8)` + `DeviceCount(7)` + `ConnectionCount(8)` + N×device + N×conn |
| 0x02 | packetACK                 | `PacketCounter(10)`                                                            |
| 0x03 | firmwareUpdateACK         | `FirmwareUpdateACKCode(7)` + `FirmwareUpdateACKDetail(32)`                     |
| 0x04 | deviceTopologyExtend      | (begin과 동일 — 토폴로지가 한 패킷에 안 들어가면 분할)                             |
| 0x05 | deviceTopologyEnd         | `ProtocolVersion(8)` (분할 종료 마커)                                          |
| 0x06 | deviceVersion             | `TopologyIndex(7)` + `length(7)` + `length×7-bit chars`                        |
| 0x07 | deviceName                | 동일 형식                                                                      |
| 0x10 | touchStart                | `PacketTimestampOffset(5)` + `TouchIndex(5)` + X(12) + Y(12) + Z(8)            |
| 0x11 | touchMove                 | 동일                                                                            |
| 0x12 | touchEnd                  | 동일                                                                            |
| 0x13 | touchStartWithVelocity    | 위 + Vx(8) + Vy(8) + Vz(8)                                                     |
| 0x14 | touchMoveWithVelocity     | 동일                                                                            |
| 0x15 | touchEndWithVelocity      | 동일                                                                            |
| 0x18 | configMessage             | `ConfigCommand(4)` + (커맨드별 payload)                                         |
| 0x20 | controlButtonDown         | `PacketTimestampOffset(5)` + `ControlButtonID(12)`                             |
| 0x21 | controlButtonUp           | 동일                                                                            |
| 0x28 | programEventMessage       | 32-bit × 3                                                                     |
| 0x30 | logMessage                | 남은 비트를 7-bit chars로 (ASCII 문자열)                                         |

### Topology device entry (16 bytes serial + 13 bits)

```
[serial: 16 × 7 bits = 112 bits]   # ASCII serial number, 16 chars
[topologyIndex: 7 bits]
[batteryLevel: 5 bits]              # 0..31
[batteryCharging: 1 bit]
```

총 125 bits per device. `maxBlocksInTopologyPacket = 6`.

**Serial number prefix → device type 매핑**:

| Prefix | Type                   |
|--------|------------------------|
| `LPB`, `LPM` | Lightpad (Pad) Block |
| `LIC`  | Live Block             |
| `LOC`  | Loop Block             |
| `DCB`  | Developer Control Block|
| `TCB`  | Touch Block            |
| `SBB`  | Seaboard Block         |
| `LKB`  | LUMI Keys Block        |

### Topology connection entry (24 bits)

```
[device1 idx: 7][port1: 5][device2 idx: 7][port2: 5]
```

Port: 5비트 인덱스. 디바이스의 가장자리(N/E/S/W)별로 시계방향으로 0부터.

`maxConnectionsInTopologyPacket = 24`.

### Touch absolute timestamp

`absolute_ts = packetTimestamp + timeOffset` (둘 다 ms).

### Touch 좌표 스케일

- X, Y: 12 bit unsigned (0..4095). `toUnipolarFloat` = value / 4095. Lightpad는 15×15 LED 그리드지만 터치 해상도는 4096×4096.
- Z (압력): 8 bit (0..255).
- Velocity (Vx/Vy/Vz): 8 bit each. **bipolar** 일 가능성 — `toBipolarFloat` 은 부호 비트 확장. 검증 필요.

## 5. Host → Device 메시지 카탈로그

`MessageFromHost`:

| Hex  | 이름                  | 본문 |
|------|----------------------|------|
| 0x01 | deviceCommandMessage | `DeviceCommand(9)` |
| 0x02 | sharedDataChange     | `PacketIndex(16)` + 시퀀스 of `DataChangeCommand(3)` + 명령별 비트 |
| 0x03 | programEventMessage  | 32 × 3 |
| 0x04 | firmwareUpdatePacket | `FirmwareUpdatePacketSize(7)` + size×7-bit |
| 0x10 | configMessage        | `ConfigCommand(4)` + 명령별 payload |
| 0x11 | factoryReset         | (없음) |
| 0x12 | blockReset           | (없음) |
| 0x20 | setName              | `length(7)` + length×7-bit chars + `0(7)` 종결 |

### DeviceCommands (9-bit `DeviceCommand`)

| Value | Name |
|-------|------|
| 0x00 | beginAPIMode |
| 0x01 | requestTopologyMessage |
| 0x02 | endAPIMode |
| 0x03 | ping |
| 0x04 | debugMode |
| 0x05 | saveProgramAsDefault |

⚠ `Protocol Format.txt` 에는 8비트로 잘못 나와 있음. **9비트가 정답**.

### Special raw SysEx (host → device)

```
resetMaster = F0 00 21 10 49 F7
```
정상 패킷 포맷 안 따르는 단일 메시지. 마스터 블록 리셋 전용.

### Shared Data Change 명령 (3비트 `DataChangeCommand`)

| Value | Name |
|-------|------|
| 0 | endOfPacket |
| 1 | endOfChanges |
| 2 | skipBytesFew (4-bit count) |
| 3 | skipBytesMany (8-bit count) |
| 4 | setSequenceOfBytes (반복: 8-bit value + 1-bit continue) |
| 5 | setFewBytesWithValue (4-bit count + 8-bit value) |
| 6 | setFewBytesWithLastValue (4-bit count) |
| 7 | setManyBytesWithValue (8-bit count + 8-bit value) |

LED 제어/공유 메모리 쓰기는 모두 이 메시지를 통해. 상세는 M5에서.

## 6. 핸드셰이크 / 라이프사이클

SDK가 디바이스 발견 후 수행하는 시퀀스 (`ConnectedDeviceGroup`):

1. **MIDI device discovery**: 이름에 `" BLOCK"` 또는 `" Block"` 포함된 input/output 짝 찾기. 입출력 이름이 같은 것끼리 페어링.
2. **Topology 요청**: master(devIdx=0)에 `requestTopologyMessage(0x01)` 전송.
3. **Topology 수신**: `deviceTopology` (+ 옵션: `deviceTopologyExtend` × N + `deviceTopologyEnd`) 받아서 디바이스 목록 구축. `numDevices < maxBlocksInTopologyPacket && numConnections < maxConnectionsInTopologyPacket` 이면 한 패킷으로 끝났다는 신호.
4. **API 모드 진입**: 각 블록(개별 topology index)에 `endAPIMode` 후 `beginAPIMode` 전송. (왜 endAPIMode 먼저? 이전 세션 잔재 정리.)
5. **Ping 루프**: 마스터는 **400ms 마다**, DNA(연결된 다른 블록)는 **1666ms 마다** `ping(0x03)` 전송. 디바이스가 6초 동안 ping 없으면 disconnect 처리. 호스트도 ping 응답 못 받으면 disconnect.
6. **이벤트 수신**: API 모드 active 인 동안 touch / button / programEvent / configMessage 등 수신.

### Ping 간격 상수

```
masterPingIntervalMs = 400
dnaPingIntervalMs    = 1666
pingTimeoutSeconds   = 6.0
apiModeHostPingTimeoutMs = 5000  # 디바이스 측 타임아웃
```

호스트 ping 안 보내면 디바이스가 5초 후 API 모드 자동 종료.

## 7. 디바이스 모델 정보 (`BlockDataSheet`)

| Type            | Serial prefix | Size (units) | LED grid | Touch | Notes |
|-----------------|---------------|--------------|----------|-------|-------|
| Lightpad        | LPB / LPM     | 2×2          | 15×15    | yes   | mode button |
| Live Block      | LIC           | 2×1          | LED row 15 | no  | 10 buttons |
| Loop Block      | LOC           | 2×1          | LED row 15 | no  | 10 buttons |
| Dev Ctrl Block  | DCB           | 2×1          | LED row 15 | no  | 8 generic + up/down |
| Touch Block     | TCB           | 2×1          | LED row 15 | no  | sensitivity buttons |
| Seaboard Block  | SBB           | 6×3          | none     | yes   | 24 keywaves |
| LUMI Keys Block | LKB           | 6×3          | none     | yes   | 24 keywaves, scale/key configs |

Memory budgets:
- Pad/Seaboard: `padBlockProgramAndHeapSize = 7200`, stack 800
- Control: `controlBlockProgramAndHeapSize = 3000`, stack 800

## 8. LED 제어 (요약 — M5에서 상세)

LED 제어는 단일 메시지가 아님:
1. LittleFoot 프로그램(예: `BitmapLEDProgram`)을 디바이스에 업로드.
2. 호스트가 픽셀 ARGB를 **공유 메모리(remoteHeap)** 에 쓰면 디바이스 측 LittleFoot 프로그램이 그걸 읽어 LED를 그림.
3. 공유 메모리 쓰기는 위의 `sharedDataChange (0x02)` 메시지로 수행 (skip/setBytes 패턴으로 변경분만 효율적으로 전송).

`numProgramMessageInts = 3` — programEventMessage 한 건당 32-bit 정수 3개.

## 9. Python 포트 시 위험/주의점

1. **MidiInput/Output 페어링**: macOS CoreMIDI는 같은 디바이스에 in/out이 따로 노출됨. 이름 매칭으로 짝지어야 함. 같은 모델이 여러 대면 occurrence 카운터로 구분.
2. **InterProcessLock**: SDK는 같은 BLOCK 디바이스를 두 프로세스가 동시에 못 잡게 OS 락을 거는데, 우리가 SDK와 동시에 쓰면 둘 다 disconnect 됨. 테스트 시 ROLI Dashboard / BLOCKS Code 종료 필수.
3. **타이밍**: ping 안 보내면 5초 후 디바이스가 API 모드 종료. `MASTER_PING_INTERVAL_MS=400` 데몬 스레드 필요.
4. **9비트 DeviceCommand**: `Protocol Format.txt` 에 8비트로 잘못 적힘. 9비트가 정답. 8비트로 보내면 모든 명령 실패.
5. **bit-packing endianness**: byte 내에서는 LSB-first, 정수 다중-바이트도 LSB-first. 단위 테스트로 round-trip 검증 필수.
6. **PacketIndex 자동 증가**: host→device sharedDataChange 마다 1씩 증가. 디바이스는 `packetACK (10-bit PacketCounter)` 으로 응답. 재전송 처리 안 해도 PoC 는 동작하지만, 활성 터치 중 신뢰성 떨어짐 (11장 참고).
7. **다중 송신자 직렬화**: ping 데몬과 user 코드가 동시에 `MidiOut.send_message` 호출하면 macOS 에서 일부 메시지 누락. `threading.Lock` 으로 직렬화 필수.

## 10. 참고 파일 (SDK)

- `protocol/roli_BitPackingUtilities.h` — bitstream + checksum
- `protocol/roli_BlocksProtocolDefinitions.h` — 모든 enum, 비트 폭, 상수
- `protocol/roli_HostPacketBuilder.h` — host→device 인코더
- `protocol/roli_HostPacketDecoder.h` — device→host 디코더
- `protocol/roli_BlockModels.h` — 디바이스별 메타데이터
- `littlefoot/roli_LittleFootRunner.h` — 디바이스 측 program/heap 레이아웃
- `littlefoot/roli_LittleFootRemoteHeap.h` — host 측 heap 동기화 (ACK + 250ms 재전송)
- `topology/internal/roli_ConnectedDeviceGroup.cpp` — 핸드셰이크 / 라이프사이클
- `topology/internal/roli_BlockImplementation.cpp` — ping 루프 (line 534+), loadProgram (line 302+)
- `topology/internal/roli_MIDIDeviceDetector.cpp` — MIDI 디바이스 발견
- `visualisers/roli_BitmapLEDProgram.cpp` — LED 그리드 LittleFoot 소스 + setLED 매핑

## 11. 실측으로 발견한 펌웨어 동작 (SDK 헤더 외)

이 절은 SDK 코드만 보고는 알 수 없는, 실디바이스 테스트로 발견한 사항들. Lightpad Block (LPM) 펌웨어 1.1.0 기준.

### 11.1 LittleFoot 프로그램 메모리 레이아웃

`getProgramHeapStart() = getProgramAndDataStart() + program.getProgramSize()` (`LittleFootRunner.h:851`).

전체 heap은 두 영역으로 나뉨:
- **bytes [0, programSize)**: 컴파일된 LittleFoot 바이트코드. 첫 4바이트가 헤더:
  - `[0..1]`: stored checksum (16-bit, little-endian)
  - `[2..3]`: program size (16-bit, little-endian) — `getProgramSize()` 의 값
  - `[4..5]`: num functions
  - `[6..9]`: heap size 등 메타데이터
- **bytes [programSize, programSize + heapSize)**: LittleFoot 가 `getHeapBits/setHeapByte` 로 접근하는 데이터 영역. `BitmapLEDProgram` 의 경우 `15*15*2 = 450` 바이트의 RGB565 픽셀 배열.

`setDataByte(index, value)` (host→device sharedDataChange 처리) 의 핵심 분기:
```cpp
if (index < program.getProgramSize() && dest != value)
    heapStart = nullptr; // force re-init
```
프로그램 영역에 다른 값을 쓰면 `heapStart = nullptr` 로 무효화. 다음 tick 에서 `program.checksumMatches()` 가 통과하면 재초기화. 즉:
- 프로그램 재업로드 → 자동 재초기화 (정상 동작)
- skip 계산이 틀어져 프로그램 영역에 잘못 쓰면 → 프로그램 죽음 (LED 안 켜짐)

### 11.2 BitmapLEDProgram 픽셀 비트 레이아웃

데이터 영역에서 픽셀 `(x, y)` 의 16비트 RGB565 위치:
```
bit_offset = (x + y * 15) * 16
[bit+0..bit+4]    R (5 bits, low)
[bit+5..bit+10]   G (6 bits)
[bit+11..bit+15]  B (5 bits, high)
```

LittleFoot 측 `repaint()` 는 매 프레임 이 데이터를 읽어 `fillPixel(makeARGB(...), x, y)` 호출. 호스트는 sharedDataChange 로 이 영역에 비트 단위 쓰기 (`setDataBits` 라우팅).

### 11.3 ⚠ Heap 데이터 영역 사용 전 0 으로 클리어 필수

**증상**: `load_bitmap_program` 후 `set_pixel` + `flush_grid` 로 한 픽셀만 갱신해도 화면에 garbage 가 나타남.

**원인**: 디바이스의 데이터 영역 (`programSize+`) 에 이전 세션의 픽셀이 남아 있음. USB 재연결로도 휘발 안 됨 (펌웨어가 어딘가 보존). 우리 `_last_sent_bits` 가 zeros 라고 가정하면 incremental diff 가 잘못된 baseline 위에서 계산됨.

**해결**: `loadProgram` 시 SDK 처럼 두 단계 송신:
1. `sharedDataChange` 로 전체 heap (`padBlockProgramAndHeapSize = 7200` 바이트) 을 0 으로 fill
2. 그 다음 program bytes 를 offset 0 에 write

`BlocksSDK/topology/internal/roli_BlockImplementation.cpp:loadProgram` 의 `clearTargetData()` 단계를 빠뜨리면 안 됨.

### 11.4 ⚠ 활성 터치 중 sharedDataChange 누락

**증상**: 사용자가 터치 중일 때 host→device sharedDataChange 메시지가 자주 누락됨. 송신 직후 packetACK 도 안 옴. 터치 안 할 때나 `touch_end` 직후엔 정상 처리.

**원인 추정**: 디바이스가 outbound touch 보고에 바빠서 inbound MIDI 입력 버퍼 처리가 밀림 (또는 우선순위 낮음). 펌웨어 소스 없이 정확한 메커니즘은 불명.

**해결책 두 가지**:

**A) Deferred reveal — `only_when_idle` 게이팅**
- 활성 터치 카운트가 0 일 때만 flush
- `touch_end` 후 ~30ms settle 시간 두기 (디바이스 outbound burst 마무리 대기)
- 페인트/스탬프류 UX 에 자연스러움 (손가락 가려져 어차피 안 보이고, 떼면 한 번에 나타남)
- `examples/touch_to_color.py` 가 이 방식

**B) Real-time — full overwrite + throttle**
- 매 송신마다 데이터 영역 전체 (450 바이트) 를 setSequenceOfBytes 로 보냄 (incremental diff 안 함)
- 100ms throttle
- 일부 패킷 떨어져도 다음 송신 한 번 통과되면 즉시 동기화 — self-contained 라서 순서 의존성 없음
- `examples/draw.py` 가 이 방식

SDK 의 진짜 정답은 packetACK 추적 + 250ms 재전송 (`LittleFootRemoteHeap.h`). 우리는 안 구현했지만 실시간 + 신뢰성 둘 다 필요하면 그 방향이 정도.

### 11.5 다중 송신자 직렬화 필요

ping 데몬 스레드와 user 코드가 동시에 `MidiOut.send_message` 호출하면 macOS CoreMIDI 단에서 일부 누락 관측됨. `Transport._send_lock` 으로 직렬화. 같은 디바이스 핸들에 대한 동시 호출은 항상 락 걸 것.

### 11.6 PacketIndex 충돌 주의

`load_bitmap_program` 이 두 번의 sharedDataChange (heap clear + program upload) 를 보냄. 그 다음 user 의 첫 LED 송신이 같은 packet_index 를 쓰면 디바이스가 dedup 함. 클래스 레벨 monotonic 카운터로 모든 송신자가 다른 index 사용하도록 할 것 (`BlockGroup._next_pkt_idx`).

