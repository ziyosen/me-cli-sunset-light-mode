from app.menus.package import show_package_details
from app.service.auth import AuthInstance
from app.menus.util import clear_screen, pause
from app.service.bookmark import BookmarkInstance, resolve_bookmark_option_code
from app.client.engsel import get_family

def show_bookmark_menu():
    api_key = AuthInstance.api_key
    tokens = AuthInstance.get_active_tokens()
    
    in_bookmark_menu = True
    while in_bookmark_menu:
        clear_screen()
        print("-------------------------------------------------------")
        print("Bookmark Paket")
        print("-------------------------------------------------------")
        bookmarks = BookmarkInstance.get_bookmarks()
        if not bookmarks or len(bookmarks) == 0:
            print("Tidak ada bookmark tersimpan.")
            pause()
            return None
        
        for idx, bm in enumerate(bookmarks):
            print(f"{idx + 1}. {bm['family_name']} - {bm['variant_name']} - {bm['option_name']}")
        
        print("00. Kembali ke menu utama")
        print("000. Hapus Bookmark")
        print("-------------------------------------------------------")
        choice = input("Pilih bookmark (nomor): ")
        if choice == "00":
            in_bookmark_menu = False
            return None
        elif choice == "000":
            del_choice = input("Masukan nomor bookmark yang ingin dihapus: ")
            if del_choice.isdigit() and 1 <= int(del_choice) <= len(bookmarks):
                del_bm = bookmarks[int(del_choice) - 1]
                BookmarkInstance.remove_bookmark(
                    del_bm["family_code"],
                    del_bm["is_enterprise"],
                    del_bm["variant_name"],
                    del_bm["order"],
                )
            else:
                print("Input tidak valid. Silahkan coba lagi.")
                pause()
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(bookmarks):
            selected_bm = bookmarks[int(choice) - 1]
            family_code = selected_bm["family_code"]
            is_enterprise = selected_bm["is_enterprise"]
            
            option_code = (selected_bm.get("package_option_code") or "").strip()
            if not option_code:
                family_data = get_family(api_key, tokens, family_code, is_enterprise)
                if not family_data:
                    print("Gagal mengambil data family.")
                    pause()
                    continue
                option_code = resolve_bookmark_option_code(family_data, selected_bm)

            if option_code:
                print(f"{option_code}")
                show_package_details(api_key, tokens, option_code, is_enterprise)
            else:
                print("Paket bookmark tidak ditemukan di API.")
                pause()
            
        else:
            print("Input tidak valid. Silahkan coba lagi.")
            pause()
            continue