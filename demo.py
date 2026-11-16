from webshell import report, scan, whitelist

FILES = {
    "upload/avatar.php": "<?php\necho eval($_POST['c']);\n",
    "upload/loader.php": "<?php\n$f = 'ev'.'al';\n$f($_POST['c']);\n",
    "upload/util.php": "<?php\nreturn 1;\n",
}

print(scan(FILES))
print(report())
print(scan(FILES)["files"])
print(whitelist("upload/avatar.php"))

# TODO: 第二段演示（实现后补）
