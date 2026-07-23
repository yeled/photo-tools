flatten-photos: main.swift Info.plist
	swiftc -O main.swift -o flatten-photos -framework Photos \
		-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist

clean:
	rm -f flatten-photos

.PHONY: clean
