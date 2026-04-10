from shared import resolve_rgb_exp01_names

samples = [
    "STM_1Ha_A2S_50m_85f75s_3mps",
    "STM_1Ha_A2S_50m_85f75s_3mps-DJIFP",
]

for s in samples:
    for enabled in [True, False]:
        names = resolve_rgb_exp01_names(s, enabled)
        print("INPUT :", s)
        print("FILTER:", enabled)
        print("BASE  :", names.base_id)
        print("T1    :", names.task1_name)
        print("T2    :", names.task2_name)
        print("-" * 50)