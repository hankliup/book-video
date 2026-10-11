# 成片交付与片头书单

6. After a final render or preview succeeds, embed the actual local media in the same reply with an absolute-path Markdown media reference, for example `![最终视频](/absolute/path/to/final.mp4)`. Never provide only a filesystem path and ask the user to locate or open the file. Include the path and brief technical metadata below the preview as supplementary text.

The intro book list is a fixed six-book template list stored in `templates/shared-video-template/intro/default-book-list.json`. It is independent of `book-pipeline.example.csv`, and the target book may appear in the rolling list before the final reveal. Placeholder labels such as `书名一` or `作者一` are forbidden.

If the user explicitly requests fully automatic production, the script approval gate may be skipped for that episode only.
