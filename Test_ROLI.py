import mido

with mido.open_input('ROLI Lightpad BLOCK 0') as inp:
    for msg in inp:
        if msg.type == 'note_on' and msg.velocity > 0:
            print(msg.note)