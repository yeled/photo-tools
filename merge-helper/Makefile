merge-helper: main.swift Info.plist
	swiftc -O main.swift -o merge-helper -framework Photos \
		-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist

clean:
	rm -f merge-helper

.PHONY: clean
