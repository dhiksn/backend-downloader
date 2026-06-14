
// From SnapSaverUnoffical (https://github.com/gitnasr/SnapSaverUnoffical)
const https = require('https');

class SnapSaveDecoder {
    decodeSnapApp(args) {
        let [encodedContent, u, charMap, subtractValue, base, decodedResult] = args;
        
        const decodeNumber = (value, fromBase, toBase) => {
            const charset = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/".split("");
            const fromCharset = charset.slice(0, fromBase);
            const toCharset = charset.slice(0, toBase);
            
            let decimal = value.split("").reverse().reduce((sum, char, index) => {
                if (fromCharset.indexOf(char) !== -1)
                    return sum += fromCharset.indexOf(char) * (Math.pow(fromBase, index));
                return sum;
            }, 0);
            
            let result = "";
            while (decimal > 0) {
                result = toCharset[decimal % toBase] + result;
                decimal = (decimal - (decimal % toBase)) / toBase;
            }
            return result || "0";
        };
        decodedResult = "";
        for (let i = 0, len = encodedContent.length; i < len; i++) {
            let segment = "";
            while (encodedContent[i] !== charMap[Number(base)]) {
                segment += encodedContent[i];
                i++;
            }
            
            for (let j = 0; j < charMap.length; j++) {
                segment = segment.replace(new RegExp(charMap[j], "g"), j.toString());
            }
            
            decodedResult += String.fromCharCode(decodeNumber(segment, base, 10) - subtractValue);
        }
        return this.fixEncoding(decodedResult);
    }

    fixEncoding(str) {
        const bytes = new Uint8Array(str.split("").map(char => char.charCodeAt(0)));
        return new TextDecoder("utf-8").decode(bytes);
    }

    getEncodedSnapApp(data) {
        // Try multiple patterns to get the encoded args
        let result;
        
        // Pattern 1: Original pattern
        if (data.includes("decodeURIComponent(escape(r))}(")) {
            result = data.split("decodeURIComponent(escape(r))}(")[1]
                .split("))")[0]
                .split(",")
                .map(v => v.replace(/"/g, "").trim());
        }
        
        // Pattern 2: For newer obfuscated versions
        if (!result) {
            // Try to find the eval call with the encoded data
            const evalMatch = data.match(/eval\(function\(h,u,n,t,e,r\)[\s\S]*?\("([\s\S]*?)"\)/);
            if (evalMatch) {
                // We need to execute the obfuscated code to get the decoded HTML
                // So we'll use a VM to run it
                const vm = require('vm');
                const script = new vm.Script(data);
                const context = {
                    document: {
                        getElementById: (id) => {
                            return {
                                innerHTML: ""
                            };
                        }
                    }
                };
                try {
                    script.runInNewContext(context);
                    // If we get here, maybe the code modified document.body.innerHTML
                    // Let's check if the context has the decoded HTML
                } catch (e) {
                    // Maybe the code throws an error, but we can extract the decoded content from the error?
                }
            }
        }
        
        return result;
    }

    getDecodedSnapSave(data) {
        // Try multiple patterns
        let patterns = [
            'getElementById("download-section").innerHTML = "',
            "getElementById('download-section').innerHTML = '",
            '.innerHTML = "'
        ];
        
        for (let pattern of patterns) {
            if (data.includes(pattern)) {
                let part1 = data.split(pattern)[1];
                let endPatterns = ['"; document.getElementById("inputData").remove(); ', '\'; document.getElementById("inputData").remove(); ', '";', '\';'];
                for (let endPattern of endPatterns) {
                    if (part1.includes(endPattern)) {
                        return part1.split(endPattern)[0].replace(/\\(\\)?/g, "");
                    }
                }
            }
        }
        
        return null;
    }

    decrypt(data) {
        // First try to run the entire response as JavaScript to get the decoded HTML
        const vm = require('vm');
        
        let decodedHtml = null;
        
        const context = {
            document: {
                getElementById: (id) => {
                    return {
                        set innerHTML(value) {
                            decodedHtml = value;
                        }
                    };
                },
                body: {
                    innerHTML: ""
                }
            }
        };
        
        try {
            const script = new vm.Script(data);
            script.runInNewContext(context, { timeout: 5000 });
        } catch (e) {
            // Maybe timeout, but we might have decodedHtml
        }
        
        if (decodedHtml) {
            return decodedHtml;
        }
        
        // If that fails, try the original method
        try {
            return this.getDecodedSnapSave(this.decodeSnapApp(this.getEncodedSnapApp(data)));
        } catch (e) {
            console.error("Original decryption failed:", e);
            return null;
        }
    }
}

// Read from stdin
let input = "";
process.stdin.on('data', (chunk) => {
    input += chunk;
});

process.stdin.on('end', () => {
    const decoder = new SnapSaveDecoder();
    const decoded = decoder.decrypt(input);
    if (decoded) {
        console.log(decoded);
    } else {
        console.error("Failed to decrypt");
        process.exit(1);
    }
});
