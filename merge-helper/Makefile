merge-helper: main.swift Info.plist
	swiftc -O main.swift -o merge-helper -framework Photos \
		-Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist
# Re-sign ad-hoc with an explicit identifier. swiftc's linker signature takes
# its identifier from the output filename, which does not match the plist's
# CFBundleIdentifier, so TCC sees an unfamiliar program after every rebuild
# and can abort the Photos request instead of reusing the existing grant.
	codesign --force --sign - --identifier com.yeled.merge-helper merge-helper

clean:
	rm -f merge-helper

.PHONY: clean
