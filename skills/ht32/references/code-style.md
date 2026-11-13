# HT32 代码风格（分层：FWLib 驱动 + C 库应用）

> 本文件是 `ht32` 主 Skill 的内部参考（无 frontmatter，不是独立宿主 Skill），由主入口按需读取，
> 不是一处可自行发挥的建议清单。
>
> **取证口径**：驱动层条目取自 `HT32_STD_5xxxx_FWLib` 官方库头文件与 example 实测（2 空格、
> Allman、`else` 独占一行、分隔线 110 列、`Module_PascalCase`、`g` 前缀）；应用层条目取自
> 现有可复用组件库的通行写法（全小写 snake_case + `_t` 类型 + `g_` 前缀 + `===` 分段）。
> 两层排版同一套 `.clang-format`，已用 clang-format 21.1.8 实测。
>
> **一句话目标**：驱动层看起来像"官方例程里多出来的一个模块"，应用层看起来像一个独立的
> 轻量 C 库；一层之内只有一套风格。

## 1. 分层与总原则

| 层 | 文件 | 判据 | 命名基准 |
|---|---|---|---|
| **驱动 / 板级** | `bsp_<module>.c/h`、`ht32f5xxxx_01_it.c` | 直接碰外设：出现 `CKCU_` / `AFIO_` / `GPIO_` / 外设库调用，或写寄存器 | 贴官方 FWLib：PascalCase |
| **应用 / 组件** | `app_<module>.c/h`、`lib/`、`core/` 内的可复用模块 | 只做业务 / 协议 / 状态机，通过 `.h` 调驱动层，可脱离具体板子复用 | C 库惯例：snake_case |

- 拿不准时按**应用层**处理（可复用优先）；一个模块只能属于一层，不在这里"看情况"。
- 应用层调用 `BSP_LED_Init()` 不算违反命名规则——那是调用外部 API，不是自研命名。

四条总原则：

1. **两层两套命名，各自内部只一套**：同一个 `.c` 里不得同时出现 PascalCase 与 snake_case
   的**自研**函数（`static` 辅助函数也算）。
2. **库 API 原样调用**：FWLib 与可复用组件的接口不改名、不做同义封装、不二次 `typedef`。
3. **排版统一**：两层同一份 `.clang-format`，不手改格式。
4. **状态进结构体**：每个模块一个 `xxx_t`，由调用方持有并把指针传进来；**不用全局变量**
   （`g_xxx` / `gXxx` 都不许），也不在模块内部散落 `static` 状态。配置参数用 `xxx_cfg_t`，
   对外只读快照用 `xxx_info_t`。
5. **可判定**：每条都要能回答"这行代码对不对"；答不了"看情况"的条目不写进本文件。

## 2. 文件与目录

| 内容 | 位置 | 说明 |
|---|---|---|
| 入口 | `main.c` | 只做初始化调度与主循环，不放业务逻辑 |
| 中断向量 | `ht32f5xxxx_01_it.c` | 向量名必须是 startup 里的原名（`USART0_IRQHandler` 等） |
| 板级驱动 | `bsp_<module>.c/.h` | 引脚、外设初始化与最底层读写，一个外设一个模块 |
| 应用逻辑 | `app_<module>.c/.h` | 状态机、协议、业务，不含寄存器操作 |
| 可复用组件 | `lib/`、`core/` | 自带 API 的独立库（状态机、协议栈等）；**只读**，不改名、不改造 |
| 库文件 | `library/`、`startup_*`、`system_*` | **只读**，不修改；需改行为时在上层封装 |
| 格式配置 | 工程根 `.clang-format` | 排版唯一事实源；由本 Skill 的 `assets/clang-format` 复制而来 |

- 一个 `.c` 对应一个同名 `.h`；模块间只通过 `.h` 暴露的接口交互。
- 头文件只放声明、宏、类型；**不得定义变量**（`.c` 里定义，`.h` 写 `extern`）。
- 头文件保护：`#ifndef __MODULE_H` / `#define __MODULE_H`（与官方 `__HT32F5XXXX_GPIO_H` 同式），
  对外头文件再加 `extern "C"` 块。

## 3. 文件骨架

两层**分段顺序相同**，分段分隔线的样式随层：

```
文件头
/* Includes */                    或 /* ==== Includes ==== */
/* Private macro */
/* Private types */
/* Private function prototypes */
/* Global variables */
/* Private variables */
/* Global functions */
/* Private functions */
```

| 层 | 文件头 | 分段分隔线 |
|---|---|---|
| 驱动 / 板级 | 110 列横幅 + `@file` / `@brief` | `/* Includes ----...----*/`（`*/` 止于第 110 列） |
| 应用 / 组件 | `/** @file ... */` 简洁块 | `/* ===== 标题 ===== */`（`=` 填充到第 82 列左右，同一文件等长） |

- 空段直接省略，不留空标题；文件头只写 `@file` + `@brief`，**不伪造**官方例程的
  `@version` / `@date` 与版权段（随官方包分发的文件除外，如可复用组件自带的头）。

## 4. 排版（由 `.clang-format` 强制，两层相同）

工程根放一份 `.clang-format`（Holtek 标准风格定制 Ver.20260825，由本 Skill 的
`assets/clang-format` 复制；新建工程由 project-create 落盘时写入，续开发发现缺失时补上），
写完即执行：

```powershell
clang-format -i --style=file bsp_led.c                  # 就地格式化
clang-format --dry-run --Werror --style=file bsp_led.c  # 只检查，不通过即报错
```

**工具已保证、不必手记**：2 空格缩进且禁用 Tab、Allman 括号、`else` 独占一行、
短块 / 短函数不合并单行、指针 `*` 贴变量名、控制语句关键字后加空格、函数调用括号前不加空格、
列宽 120 自动折行、连续赋值与连续宏定义对齐、函数体内 `#if/#endif` 跟随缩进、
include 不自动排序、注释不重排。

**工具管不了、必须手写守**：

| 项 | 规则 |
|---|---|
| 分隔线 | 驱动层 `*` 填充到第 110 列；应用层 `=` 填充等长；同一文件所有分隔线对齐 |
| 函数头 | 驱动层：横幅 + Doxygen（`*/` 止于第 110 列，空 1 格接 `/**`）；应用层：普通 `/** */` |
| 空行 | 函数之间 1 行；连续空行最多 2 行（`MaxEmptyLinesToKeep`） |
| 分段顺序 | 见第 3 节——clang-format 不分组、不排序 |
| 注释语言 | 见第 9 节 |

**两条必须直接按格式化结果写的实测偏差**（clang-format 21.1.8，`ReflowComments: false`、
`SpacesBeforeTrailingComments: 0` 均无法关闭，已逐项验证）：

1. 驱动层函数头的 `...*//**` 会被拆成 `...*/ /**` —— 直接按带空格的形式写，否则每次格式化都产生 diff。
2. 头文件的 ` extern "C" {` 会被 Allman 拆成 `extern "C"` 换行 `{` —— 直接按拆开的写
   （`IndentExternBlock: NoIndent` 只控制缩进，不控制换行）。

## 5. 命名

### 5.1 驱动 / 板级层（贴官方 FWLib）

| 对象 | 规则 | 正例 | 反例 |
|---|---|---|---|
| 函数 | `模块前缀_` + PascalCase 动词短语 | `BSP_LED_Init`、`BSP_Uart_OnRx` | `bsp_led_init`、`ledInit` |
| 类型 | `模块_t` / `模块_用途_t`（**不用** `xxx_TypeDef` / `xxx_InitTypeDef`） | `bsp_led_t`、`bsp_led_mode_t` | `BSP_LED_TypeDef`、`BSP_LED_Mode_Enum` |
| 局部变量 | PascalCase，无前缀 | `uint32_t TimeoutMs`、`USART_InitTypeDef UsartInit` | `timeout_ms`、`pucBuf` |
| 全局变量 | **禁止**——状态进模块 `xxx_t` | `bsp_led_t *led` 由调用方持有 | `gUartRxLength`、`g_uart_rx_len` |
| 文件内静态变量 | 只用于只读常量表 / 收发缓冲；**模块状态一律进 `xxx_t`** | `static const uint16_t BaudTable[]` | `static uint32_t BlinkCount`（状态） |
| 外设初始化 | `<Periph>_Configuration` | `CKCU_Configuration()` | `ckcu_config()` |
| 中断转调 | `模块前缀_On事件` | `BSP_Uart_OnRx()` | `uart0_isr` |

### 5.2 应用 / 组件层（C 库惯例）

| 对象 | 规则 | 正例 | 反例 |
|---|---|---|---|
| 函数 | `模块前缀_` + snake_case 动词短语 | `app_key_scan`、`gcp_send`、`lwfsm_tick` | `AppKeyScan`、`keyScan` |
| 类型 | `模块_t` / `模块_info_t` / `模块_cfg_t` | `app_key_t`、`gcp_info_t`、`app_key_cfg_t` | `AppKeyTypeDef`、`struct app_key` |
| 局部变量 | snake_case，无前缀 | `uint32_t elapsed_ms`、`uint8_t dev_addr` | `ElapsedMs`、`pucBuf` |
| 全局变量 | **禁止**——状态进模块 `xxx_t` | `app_key_t *ctx` 由调用方持有 | `g_dev_state`、`gDevState` |
| 文件内静态变量 | 只用于只读常量表 / 收发缓冲；**模块状态一律进 `xxx_t`** | `static const uint8_t CrcTable[]` | `static uint8_t key_state`（状态） |
| 私有函数 | `static` + `模块前缀_动词` | `static uint8_t calc_len(...)` | `CalcLen(...)` |
| 回调 | `模块前缀_on_事件` | `lwfsm_on_change` | `OnChange` |

### 5.3 两层通用

| 对象 | 规则 | 正例 |
|---|---|---|
| 枚举值 | 全大写 + 模块前缀 | `BSP_LED_MODE_BLINK`、`GCP_ERR_TIMEOUT` |
| 宏 / 常量 | 全大写 + 模块前缀 | `BSP_LED_PERIOD_MS`、`GCP_MAX_PACKET_SIZE` |
| 中断服务函数 | 向量原名（startup 里叫什么就写什么），逻辑超过约 20 行转调私有函数 | `USART0_IRQHandler` |
| 头文件保护 | `__MODULE_H` | `__BSP_LED_H`、`__APP_KEY_H` |
| 库 API | 原样调用，不改名、不同义封装 | `GPIO_DirectionConfig(...)` |

- 前缀按层取：驱动层 `BSP_`，应用层 `APP_` 或组件自有前缀（`gcp_` / `lwfsm_`）。
  新增前缀须先写进 `HT32_PROJECT.md` 第 8 节的项目追加约定。

### 5.4 模块实例化（状态放哪）

| 结构体 | 用途 | 谁持有 | 例子 |
|---|---|---|---|
| `xxx_t` | 模块运行状态（模式、计数、缓冲、句柄） | **调用方**声明并持有，指针传进每个 API | `bsp_led_t`、`app_key_t` |
| `xxx_cfg_t` | 初始化参数（引脚、周期、容量等），只在 init 入口用 | 调用方构造后传入 | `bsp_led_cfg_t`、`app_uart_cfg_t` |
| `xxx_info_t` | 对外只读快照（状态、统计、版本），由模块填充 | 调用方提供缓冲，模块写入 | `app_uart_info_t` |

- 每个模块**只有一个** `xxx_t`，模块内部不得再藏状态；非必需不要用零散变量。
- API 首参统一 `xxx_t *ctx`（实例指针），与库的 handle 风格一致：
  `void app_key_scan(app_key_t *ctx, uint32_t elapsed_ms)`。
- 实例可以声明在 `main.c` 里（`static app_key_t key;` 属调用方持有，允许），
  但**禁止**在模块 `.c` 里定义 `g_xxx` 全局变量或模块状态 `static`。
- 不新增 `xxx_InitTypeDef` / `xxx_TypeDef`：要传初始化参数用 `xxx_cfg_t`，不要另造一套。

## 6. 类型与数据宽度

- **自研数据**用 stdint 定长类型：`uint8_t` / `uint16_t` / `uint32_t` / `int32_t`。
  `char` 只表示字符，不表示数据宽度。
- **库接口签名照抄**：库参数写 `u32`/`u16`/`u8` 时不要改写成 `uint32_t`，也不要把库类型
  二次 `typedef`。二者同型——`ht32.h` 里 `typedef unsigned int u32`，与 `uint32_t` 等价，
  因此混用不会引入宽度风险，但**签名处必须保持库原样**。
- 状态量：驱动层用库类型 `FlagStatus`（`SET`/`RESET`）、`ErrStatus`（`SUCCESS`/`ERROR`）；
  应用层用 `stdbool.h` 的 `bool`。不自造 `TRUE/FALSE` 枚举。
- 配置结构体**必须**先 `= {0}` 再逐字段赋值（官方例程有 NOTICE 专门警告漏字段）。
- 初始化参数统一 `xxx_cfg_t`、只读快照统一 `xxx_info_t`；**不造** `xxx_InitTypeDef` /
  `xxx_TypeDef`（库自带的 `USART_InitTypeDef` 等照常接收，但自研不新增同类命名）。
- 不使用 `float`/`double` 做计数值；定点换算须在注释里写清单位与量程。

## 7. 库 API 与寄存器

- 能用库 API 就用库 API；直接操作寄存器只限两种情况：库未覆盖，或需要原子/时序约束，
  且必须在该行上方写 `/* 原因：... */`。
- 初始化顺序固定：**CKCU 时钟 → AFIO 复用 → 引脚/外设参数 → 使能 → NVIC**。
  时钟配置集中在驱动层的 `Xxx_Configuration()` 内，不散落在业务函数里。
- 时钟使能沿用官方写法：`CKCU_PeripClockConfig_TypeDef CKCUClock = {{0}};` +
  `CKCUClock.Bit.<Periph> = 1;` + `CKCU_PeripClockConfig(CKCUClock, ENABLE);`。
- 不修改 `library/`、`startup_*`、`system_*`、`ht32_board.*`；也不修改 `lib/`、`core/` 内的
  可复用组件（确需改行为时在上层封装，并在 `HT32_PROJECT.md` 变更记录登记）。

## 8. 中断与共享数据

- **中断上下文与实例所有权**：模块状态仍只放在调用方持有的 `xxx_t`；硬件 ISR 向量不能增加参数，
  由持有实例的上层中断适配器转调带实例指针的模块 API（驱动层如 `BSP_Uart_OnRx(ctx)`），
  不得因此在模块 `.c` 中增加全局实例。适配器与实例所有者之间的访问路径必须明确，不依赖不可见的局部变量。
- **`volatile` 的边界**：只用于 ISR 与非 ISR 共享、且确实需要每次从内存读取的对象（如单字节标志或硬件寄存器）；
  `volatile` 只保证可见性，不保证原子性、互斥或复合状态一致性。不要给整个状态结构体机械添加 `volatile`。
- **单字节标志**：单核 Cortex-M 上对齐的 8-bit 读写可作为单次访问，但“读-改-写”仍非原子；需要事件计数时用计数器或队列，不能用可能合并事件的布尔标志。
- **多字节 / 复合状态**：若 ISR 写、主循环读，读取快照时保存并恢复进入临界区前的 PRIMASK（`__get_PRIMASK()` → `__disable_irq()` → 复制 → 按原 PRIMASK 恢复）；
  或采用有明确生产者 / 消费者所有权的双缓冲 / 环形缓冲。禁止主循环直接读取 ISR 正在逐字段更新的结构体。
- **ISR 工作量**：只清标志、读取必要数据、写入有界缓冲或置事件；解析、状态机推进、日志和耗时处理留在主循环。队列满时的丢弃策略必须明确。
- 临界区只包住最短的数据复制 / 更新，不以无条件 `__enable_irq()` 结束；PRIMASK 不能屏蔽 NMI / HardFault。
  具体 CMSIS API 与编译器屏障语义先查当前工程头文件 / code MCP，确保编译器不会把共享访问移出临界区。
- **时间基准**：遵守 `HT32_PROJECT.md` 第 8 节的 BFTM0 约定，不用 SysTick 做死循环延时。
- **优先级集中配置**：NVIC 优先级与使能在初始化阶段集中完成；具体优先级范围与可用 API 先按目标内核及库取证。
- ISR 不做阻塞等待、`printf` 或浮点长运算；共享向量逐项检查已使能的中断源，按库规定清除标志。

## 9. 注释

- 函数头用 Doxygen：`@brief` 必写，有参数写 `@param`，有返回值写 `@retval`；
  一句话能说清就不写 `@details`。
- 结构体成员用 `///< 说明` 行尾注释（应用层）或 `/*!< 说明 */`（驱动层，随官方）。
- 块注释 `/* ... */`；行尾短注可用 `//`，但与代码至少隔 1 个空格，且同一文件内不混用两种
  行尾注释符。
- 注释语言：与官方例程一致用英文；工程整体改用中文可以，但**同一文件不得中英混排**，
  改语言须写进 `HT32_PROJECT.md` 第 8 节。
- 不写复述代码的注释（`i++; /* i 加一 */`）；写"为什么"和单位（`/* 10 ms, 1 tick = 1 ms */`）。
- 魔数必须命名成宏或就地注释来源；`TODO(待查证)` 保留原样并在汇报中单列。

## 10. 禁止清单

- **禁止跨层混用命名**：应用层文件里写 PascalCase 自研函数、驱动层文件里写 snake_case
  自研函数（含 `static` 辅助函数）。
- **禁止全局变量**：`g_xxx` / `gXxx` 一律不许；模块状态收进 `xxx_t`，由调用方持有并传指针。
  模块内也不得用 `static` 存状态（只允许只读常量表 / 收发缓冲）。
- **禁止自造 `xxx_TypeDef` / `xxx_InitTypeDef`**：自研类型统一 `xxx_t`，初始化参数用 `xxx_cfg_t`，
  只读快照用 `xxx_info_t`（库自带的 `USART_InitTypeDef` 等照常接收，不算违规）。
- 禁止匈牙利前缀（`pucBuf` / `i32Cnt`）、禁止 `MyXxx` / `my_xxx` 这类无模块前缀的名字。
- 禁止在头文件定义变量、禁止在 `.c` 里重复声明库函数原型。
- 禁止 `malloc` / `free` 等动态内存。
- 禁止无宏包装的散落 `printf` 调试输出；调试打印统一走工程的日志宏（retarget / printf 验证阶段可临时直接用 `printf`）。
- 禁止提交被注释掉的死代码块（要留就写 `#if 0 /* 原因 */` + 一句说明）。
- 禁止改 `library/`、`lib/`、`core/` 内文件来"顺手修一下"。

## 11. 正例 A：驱动层模块（贴 FWLib）

```c
/*********************************************************************************************************/ /**
 * @file    bsp_led.h
 * @brief   Board LED driver.
 ************************************************************************************************************/

/* Define to prevent recursive inclusion -------------------------------------------------------------------*/
#ifndef __BSP_LED_H
#define __BSP_LED_H

#ifdef __cplusplus
extern "C"
{
#endif

/* Includes ------------------------------------------------------------------------------------------------*/
#include "ht32.h"

/* Exported types ------------------------------------------------------------------------------------------*/
typedef enum
{
  BSP_LED_MODE_OFF = 0, /*!< LED off                                                              */
  BSP_LED_MODE_ON,      /*!< LED on                                                               */
  BSP_LED_MODE_BLINK    /*!< LED blinks at BSP_LED_PERIOD_MS                                      */
} bsp_led_mode_t;

typedef struct
{
  bsp_led_mode_t mode; /*!< Current mode                                                         */
  uint32_t blink_ms;   /*!< Elapsed time in blink mode (ms)                                      */
} bsp_led_t;

void BSP_LED_Init(bsp_led_t *led);
void BSP_LED_SetMode(bsp_led_t *led, bsp_led_mode_t Mode);
void BSP_LED_OnTick(bsp_led_t *led); /* called from the BFTM0 1 ms tick */

#ifdef __cplusplus
}
#endif

#endif /* __BSP_LED_H */
```

```c
/*********************************************************************************************************/ /**
 * @file    bsp_led.c
 * @brief   Board LED driver.
 ************************************************************************************************************/

/* Includes ------------------------------------------------------------------------------------------------*/
#include "bsp_led.h"
#include "ht32_board_config.h"

/* Private macro -------------------------------------------------------------------------------------------*/
#define BSP_LED_PERIOD_MS (500)

/* Global functions ----------------------------------------------------------------------------------------*/
/*********************************************************************************************************/ /**
  * @brief  Initialize the board LED pin.
  * @retval None
  ***********************************************************************************************************/
void BSP_LED_Init(bsp_led_t *led)
{
  CKCU_PeripClockConfig_TypeDef CKCUClock = {{0}};

  led->mode     = BSP_LED_MODE_OFF;
  led->blink_ms = 0;

  CKCUClock.Bit.AFIO                  = 1;
  CKCUClock.Bit.HTCFG_OUTPUT_LED1_CLK = 1;
  CKCU_PeripClockConfig(CKCUClock, ENABLE);

  AFIO_GPxConfig(HTCFG_OUTPUT_LED1_ID, HTCFG_OUTPUT_LED1_AFIO_PIN, AFIO_FUN_GPIO);
  GPIO_DirectionConfig(HTCFG_LED1, HTCFG_OUTPUT_LED1_GPIO_PIN, GPIO_DIR_OUT);
  GPIO_WriteOutBits(HTCFG_LED1, HTCFG_OUTPUT_LED1_GPIO_PIN, RESET);
}
```

- 以上两段是**经 `.clang-format` 格式化后的原样输出**，不是手写近似值。
- 体现：`BSP_` + PascalCase、类型 `_t`、状态在 `bsp_led_t` 里由调用方持有、110 列分隔线、
  `*/ /**` 函数头、`{0}` 初始化、连续赋值对齐、库 API 原样调用。

## 12. 正例 B：应用层模块（C 库惯例）

```c
/**
 * @file    app_key.h
 * @brief   Key scan and debounce.
 */

#ifndef __APP_KEY_H
#define __APP_KEY_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdbool.h>
#include <stdint.h>

#define APP_KEY_DEBOUNCE_MS (20)

typedef enum
{
  APP_KEY_EVENT_NONE = 0,
  APP_KEY_EVENT_DOWN,
  APP_KEY_EVENT_UP
} app_key_event_t;

typedef struct
{
  uint8_t pin_state;  ///< Last sampled pin state
  uint16_t stable_ms; ///< Time the new state has been held (ms)
} app_key_t;

void app_key_init(app_key_t *key);
app_key_event_t app_key_scan(app_key_t *key, bool pin_level, uint16_t elapsed_ms);

#ifdef __cplusplus
}
#endif

#endif /* __APP_KEY_H */
```

```c
/**
 * @file    app_key.c
 * @brief   Key scan and debounce.
 */

#include "app_key.h"

/* =================================================================================
 * Global functions
 * ================================================================================= */

void app_key_init(app_key_t *key)
{
  key->pin_state = 0;
  key->stable_ms = 0;
}

/* =================================================================================
 * Scan one key, return the event of this slice.
 * ================================================================================= */

app_key_event_t app_key_scan(app_key_t *key, bool pin_level, uint16_t elapsed_ms)
{
  app_key_event_t event = APP_KEY_EVENT_NONE;
  uint8_t level         = pin_level ? 1U : 0U;

  if (level != key->pin_state)
  {
    key->stable_ms += elapsed_ms;
    if (key->stable_ms >= APP_KEY_DEBOUNCE_MS)
    {
      key->pin_state = level;
      key->stable_ms = 0;
      event          = level ? APP_KEY_EVENT_UP : APP_KEY_EVENT_DOWN;
    }
  }
  else
  {
    key->stable_ms = 0;
  }

  return event;
}
```

- 同样经 `.clang-format` 验证、幂等。
- 体现：`app_` + snake_case、`xxx_t`、局部变量 snake_case、`===` 分段、`///<` 成员注释、
  无 `static` 全局状态（实例由调用方持有，便于多实例与 PC 侧编译）。

## 13. 反例速查

> 缩进、括号、`else` 位置、指针空格、单行块、折行等**已被 `.clang-format` 自动修正**，
> 不列在这里；下表只收工具改不掉的。

| 反例 | 问题 | 改成 |
|---|---|---|
| 应用层写 `AppKeyScan()` | 跨层混用命名 | `app_key_scan()` |
| 驱动层写 `bsp_led_init()` | 跨层混用命名 | `BSP_LED_Init()` |
| `BSP_LED_TypeDef` / `xxx_InitTypeDef` | 自造 TypeDef | `bsp_led_t` / `bsp_led_cfg_t` |
| `gUartRxLen` / `g_dev_state` | 全局变量 | 状态收进 `app_uart_t`，由调用方持有 |
| `static uint32_t BlinkCount;`（存状态） | 模块状态散落 | 放进 `bsp_led_t` 成员 |
| `int timeout;` | 裸 `int` 表宽度 | `int32_t TimeoutMs`（驱动层）/ `int32_t timeout_ms`（应用层） |
| ` extern "C" {` | 会被 clang-format 拆行，每次产生 diff | `extern "C"` 换行 `{` |
| 同文件分隔线长短不一 | 横幅不对齐 | 驱动层填充到 110 列；应用层 `=` 等长 |
| `/* 初始化 LED */` 与 `/* init UART */` 同文件 | 中英混排 | 整文件统一一种语言 |
| `printf("dbg %d\n", x);` | 散落调试输出 | 工程日志宏 |
| 头文件里写 `uint8_t Buf[10];` | 头文件定义变量 | `.h` 写 `extern`，`.c` 写定义 |

## 14. 与 `HT32_PROJECT.md` / `.clang-format` 的关系

- 三层事实源，从上到下：`.clang-format`（排版，机器执行）→ 本文件（分层 / 命名 / 注释 / 结构）
  → `HT32_PROJECT.md` 第 8 节（工程落地摘要 + 项目追加约定）。
- 项目追加约定可以加严，不可放宽第 1 节总原则与第 10 节禁止清单；冲突时以
  `HT32_PROJECT.md` 第 8 节为准。
- **唯一例外：既有工程的风格基线**。工程内已有自研代码且风格与本文件不一致时，续开发必须先
  询问用户二选一（沿用工程现有风格 / 采用本规范），并把结果写进第 8 节「项目追加约定」。
  选择「沿用工程现有风格」后，命名 / 排版 / 注释 / 文件骨架以现有代码为准，本文件只作参考；
  类型宽度、中断与共享数据、第 10 节中的安全条目（动态内存、头文件定义变量、改库文件、死代码）
  仍按本文件执行。既有文件不批量重排。
- 工程里若引入自有可复用组件，其内部风格按组件原样保留（只读），**不要为了统一而改名**。
- `.clang-format` 的版本（`Ver.20260825`）与 skill 一并下发，工程内那份应与之内容一致；
  改动本文件或 `.clang-format` 需同步刷新 `HT32_PROJECT.md` 第 7 节快照中的 skill 版本。
