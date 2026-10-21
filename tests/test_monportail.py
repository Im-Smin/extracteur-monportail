from monportail import report
from monportail.discovery import page_videos, playlist_entries
from monportail.downloads import video_paths
from monportail.inventory import CourseVideos, Inventory, Video

BLOCK = "/contenu/sitescours/042/04207/202601/site182052/modules1443863/module1808527/page4908962/blocaudiovideo527802"
URL_1 = f"{BLOCK}/item926284/dossier.ANI1005_sem11_capsule01_IK.mov/video.mp4?identifiant=138b963a"
URL_2 = f"{BLOCK}/item926285/dossier.ANI1005_sem11_capsule02_RF.mov/video.mp4?identifiant=ffff0000"
HOST = "https://sitescours.monportail.ulaval.ca"


def capsule(item, folder, title, duration):
    return (
        f'<div class="Item_AudioVideo ul_div"><div class="Vignette_Video"><a class="af_commandLink" href="#"></a>'
        f'<img class="ul_imageFallback" src="{BLOCK}/item{item}/dossier.{folder}.mov/vignette01.png?identifiant=z"></div>'
        f'<div class="ul_div"><a class="Item_AudioVideo_Titre af_commandLink" href="#"><span>{title}</span></a>'
        f'<p class="ContenuRiche_InfoSecondaire">{duration} minutes</p></div></div>'
    )


PAGE = (
    f'<div class="jw-media"><video class="jw-video" src="{URL_1}"></video></div>'
    + capsule(926284, "ANI1005_sem11_capsule01_IK", "01. Système IK", "12:48")
    + capsule(926285, "ANI1005_sem11_capsule02_RF", "02. Reverse foot setup", "21:08")
)
PLAYER = [
    {"source": "jwplayer", "url": HOST + URL_1, "title": "", "duration": 768},
    {"source": "jwplayer", "url": HOST + URL_2, "title": "", "duration": 0},
]


def test_playlist_titles_and_durations():
    assert playlist_entries(PAGE) == {
        "926284": {"title": "01. Système IK", "duration": "12:48"},
        "926285": {"title": "02. Reverse foot setup", "duration": "21:08"},
    }


def test_unknown_layout_falls_back_to_nearby_text():
    html = f'<table><tr><td><img src="{BLOCK}/item7/dossier.x.mov/vignette01.png"></td><td>3. Hiérarchie <i>9:23 minutes</i></td></tr></table>'
    assert playlist_entries(html) == {"7": {"title": "3. Hiérarchie", "duration": "9:23"}}


def test_player_and_page_are_merged_without_duplicates_or_thumbnails():
    videos = page_videos(PAGE, PLAYER)
    assert [v.title for v in videos] == ["01. Système IK", "02. Reverse foot setup"]
    assert all("identifiant=" in v.url for v in videos)
    assert videos[0].original_name == "ANI1005_sem11_capsule01_IK"
    assert videos[0].extension == "mp4"


def test_html_alone_finds_the_loaded_capsule():
    videos = page_videos(PAGE, [])
    assert [v.url for v in videos] == [HOST + URL_1]


def test_tracker_links_are_decoded():
    link = "/analytique/evenement/multimedia.mp4?idFichier=1&idSite=1&url=%2Fcontenu%2Fsitescours%2Fa%2Fcapsule.mp4"
    [video] = page_videos(f'<a href="{link}">Capsule 1</a>', [])
    assert video.url == HOST + "/contenu/sitescours/a/capsule.mp4"
    assert video.title == "Capsule 1"


def course(videos):
    return CourseVideos("1", "202601", "Hiver 2026", "2026-1 Hiver", "ANI-1005 Rigging", videos=videos, scanned=True)


def test_video_paths_are_stable_unique_and_next_to_documents(tmp_path):
    def video(rank, title):
        return Video(f"https://x/contenu/{rank}/video.mp4", title, "", "orig", "mp4", ["01 - Module 1", "Théorie"], rank)

    record = course([video(1, "01. Système IK"), video(2, "Intro"), video(3, "Intro")])
    paths = video_paths(tmp_path, record)
    assert [p.name for p in paths.values()] == ["01. Système IK.mp4", "02 - Intro.mp4", "03 - Intro.mp4"]
    assert next(iter(paths.values())).relative_to(tmp_path).parts[:5] == (
        "2026-1 Hiver", "ANI-1005 Rigging", "Vidéos", "01 - Module 1", "Théorie")
    assert paths == video_paths(tmp_path, record)


def test_inventory_round_trip(tmp_path):
    Inventory(courses=[course([Video("https://x/a.mp4", "A", "1:00", "a", "mp4", ["Accueil"], 1, 1234)])]).save(tmp_path)
    loaded = Inventory.load(tmp_path)
    assert loaded.courses[0].videos[0].size == 1234
    assert loaded.courses[0].scanned


def test_report_lists_failures(tmp_path):
    rows = [report.VideoRow(report.FAILED, "Hiver 2026", "ANI", "Module 1", "A", "", None, "a.mp4", "https://x", "HTTP 404")]
    path = report.write(tmp_path, rows, ["remarque"])
    assert "HTTP 404" in path.read_text(encoding="utf-8")
    assert (tmp_path / report.CSV_NAME).exists()
